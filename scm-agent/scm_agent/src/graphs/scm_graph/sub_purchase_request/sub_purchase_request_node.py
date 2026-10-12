"""
采购申请子图的节点。

链路：欢迎 → 要素提取 + 取价 + 策略判定 →(命中需审条款)→ 人工审批 →(可改判重报)→ 执行。

HITL × 幂等的四条不变量（与收货复核链路同源，这条链路逐条复用）
------------------------------------------------------------
  ① **`interrupt()` 所在节点在 resume 时会从第一行重放**，所以中断之前不得有
     任何副作用。`approval_node` 中断前全程只读状态，下单只有一个落点 `execute_node`。
  ② **plan_id 由 biz_id（确定性 PR 单号）uuid5 生成**，重放、重试、多轮审批指向
     同一个 ID，不会每轮换一个（数量/到货仓被 patch 改动时单号才换 —— 那是
     业务语义上的「另一笔申请」，见 `_recreate_node`）。
  ③ **执行节点自己做幂等**：重复 resume 时 `IdempotencyGuard` 短路返回首次结果。
  ④ **审批记录按「线程:方案:轮次」各记一笔，同一轮内首写胜** —— 让审批节点
     本身可重放（重放不记第二笔），同时多轮 recreate 的结论不互相吞掉。

单号为什么是确定性的
--------------------
`build_request_id(物料, 数量, 到货仓, 提报人)` 四个要素拼出 `PR{日期}{6位哈希}`：
同一笔申请重发多少次都是同一个单号，plan_id、幂等键、审批记录全部落在这个锚点上。
要素改了（数量、仓库变了）就是另一笔申请，理应得到新单号 —— 这是**业务语义上**的
「另一笔」，不是重放。

审批判定的三段结论（Phase 5 全量）
---------------------------------
`ok` 通过落库 ｜ `discard` 驳回不落 ｜ `recreate` 按 patch 修改要素**重走策略引擎**：
patch 只允许改「供应商 / 数量 / 到货仓」三个字段，其余要素一律保持 ——
复判可能降为免审批直接流转，也可能仍需审批（第二次中断）。「驳回后带修改重报、
而不是从头再提一遍」就是「一次通过率」指标背后的机制。patch 缺席时按原样复判。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.sub_purchase_request.sub_purchase_request_state import (
    SubPurchaseRequestState,
    SubPurchaseRequestStateField,
)
from integrations.factory import get_purchase_port, get_supplier_port
from integrations.idempotency import (
    IdempotencyConflict,
    IdempotencyGuard,
    build_key,
    build_plan_id,
    build_request_id,
)
from integrations.normalize import normalize_purchase_order, normalize_quote
from integrations.policy import PolicyFacts, match as match_policy
from llm.llm_purchase_draft import llm_purchase_draft
from models.scm_models import MaterialInfo, NodeResult, PurchasePlan, SupplierInfo, TaskResult
from observability.progress import emit, node_done
from utils.agent_logger import get_logger

logger = get_logger("sub_purchase_request_node")

#: 供应商没给交期时按几天排预计到货 —— 兜底值而不是猜真实的，会写进方案里给人看
_DEFAULT_LEAD_TIME_DAYS = 7

#: 采购策略文件名（规则引擎按这个去 data/seed/ 取）
_POLICY_KIND = "purchase_policy"

#: recreate 允许修改的字段：只改被驳回的要素，其余一律不动。
_PATCH_FIELDS = ("supplier_code", "qty", "target_warehouse")


class NodeName(str, Enum):
    WELCOME  = "purchase_request_welcome_node"
    MATCH    = "purchase_request_match_node"
    APPROVAL = "purchase_request_approval_node"
    EXECUTE  = "purchase_request_execute_node"


# ---------------------------------------------------------------- 输入定位

def _request_text(state: SubPurchaseRequestState) -> str:
    """这次要处理的申请原文。

    菜单 id 直达（工作台上点「采购申请」按钮）时 task_input 是意图名本身，
    没有可抽取的内容 —— 这种调用要如实说「请把申请说清楚」，不能拿菜单 id
    去抽要素，更不能硬编一笔申请出来。
    """
    raw = (state.get(SubPurchaseRequestStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.PURCHASE_REQUEST.value:
        return raw
    return ""


def _operator_id(state: SubPurchaseRequestState) -> str:
    operator = state.get(SubPurchaseRequestStateField.OPERATOR_INFO)
    return getattr(operator, "operator_id", "") or ""


# ---------------------------------------------------------------- 取价

def _quotes_for(material_code: str) -> List[Dict[str, Any]]:
    """按物料拉报价（走供应商端口 + 归一）。

    报价是「按物料拉取」的明细，与供应商子图同一口径：这里刻意不读状态、
    不进装载链路 —— 采购链路要报价只是为了取价与算均价，全量报价装进状态
    没有意义（理由详见 `sub_supplier_node` 的模块注释）。

    端口失败按「无报价」降级处理（回参考价），理由会写进取价轨迹 ——
    比价依据缺失这件事要让人在审批卡上看得见，而不是悄悄换一个价格口径。
    """
    try:
        raw = get_supplier_port().list_supplier_quotes(material_code)
    except Exception as e:
        logger.warning(f"报价拉取失败，按无报价处理：{e}")
        return []
    return [normalize_quote(q) for q in raw]


def _pick_supplier(
    material: MaterialInfo,
    requested: str,
    suppliers: List[SupplierInfo],
    quotes: List[Dict[str, Any]],
) -> Tuple[Optional[SupplierInfo], float, float, List[str], str]:
    """定供应商与单价。返回 (供应商, 单价, 均价, 取价轨迹, 错误)。

    三段优先：**申请指定**（报价取价，无报价回参考价）> **自动选最低价**
    （附报价家数与均价，审批人能看到比价依据）> **参考价兜底**（无报价数据）。

    指定了不存在的供应商是唯一必须退回申请人的情形：不猜、也不静默降级成
    自动选价 —— 申请人写错了编码，应当让他改，而不是替他换一家供应商下单。
    """
    by_code = {s.supplier_code: s for s in suppliers}
    avg_price = (
        round(sum(float(q["unit_price"]) for q in quotes) / len(quotes), 4)
        if quotes else 0.0
    )
    quote_by_supplier = {q["supplier_code"]: q for q in quotes}

    if requested:
        supplier = by_code.get(requested)
        if supplier is None:
            return None, 0.0, avg_price, [], f"指定的供应商 {requested} 不在供应商主数据里"
        quote = quote_by_supplier.get(requested)
        if quote:
            price = float(quote["unit_price"])
            note = (
                f"按申请指定供应商 {supplier.supplier_name}（{requested}），"
                f"报价 {price:.2f} 元/{material.unit}"
            )
            return supplier, price, avg_price, [note], ""
        note = (
            f"按申请指定供应商 {supplier.supplier_name}（{requested}），"
            f"暂无报价，按参考价 {material.unit_price_ref:g} 元/{material.unit} 计价"
        )
        return supplier, float(material.unit_price_ref), avg_price, [note], ""

    if quotes:
        cheapest = min(quotes, key=lambda q: (float(q["unit_price"]), q["supplier_code"]))
        supplier = by_code.get(cheapest["supplier_code"])
        name = supplier.supplier_name if supplier else cheapest["supplier_code"]
        note = (
            f"未指定供应商：{len(quotes)} 家报价中按最低价选取 {name}"
            f"（{cheapest['supplier_code']}），报价 {cheapest['unit_price']:.2f} 元/{material.unit}"
            f"（同料均价 {avg_price:.2f}）"
        )
        return supplier, float(cheapest["unit_price"]), avg_price, [note], ""

    note = f"暂无供应商报价，按物料参考价 {material.unit_price_ref:g} 元/{material.unit} 计价"
    return None, float(material.unit_price_ref), avg_price, [note], ""


def _facts(
    material: MaterialInfo,
    qty: float,
    unit: str,
    unit_price: float,
    avg_price: float,
    amount: float,
    supplier: Optional[SupplierInfo],
) -> PolicyFacts:
    """装配策略引擎的输入事实。

    交期取供应商承诺值；主数据缺失时留 0 —— 策略层对 0 的口径是「未知」，
    不是「零天」（见 `policy._check_lead_time_lt`）。
    """
    return PolicyFacts(
        material_code=material.material_code,
        material_name=material.material_name,
        qty=qty,
        unit=unit,
        unit_price=unit_price,
        avg_price=avg_price,
        amount=amount,
        supplier_code=supplier.supplier_code if supplier else "",
        supplier_name=supplier.supplier_name if supplier else "",
        is_new_supplier=bool(supplier.is_new) if supplier else False,
        lead_time_days=int(supplier.lead_time_days) if supplier else 0,
    )


# ---------------------------------------------------------------- 节点：定方案

def welcome_node(state: SubPurchaseRequestState, config: RunnableConfig):
    """进要素提取之前先报一声。"""
    logger.info("=== 采购申请 ===")
    emit("policy_match", detail="准备提取采购申请要素")
    return {
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME.value,
            execute_result_code=0,
            execute_result_msg="正在核对采购要素与审批策略，请稍等。",
            execute_exceptions=[]
        )]
    }


def match_node(state: SubPurchaseRequestState, config: RunnableConfig):
    """抽要素 → 取价 → 策略判定 → 出方案。**不产生任何副作用。**"""
    logger.info("=== 采购申请要素提取 ===")

    text = _request_text(state)
    if not text:
        return _failure(
            NodeName.MATCH.value, "申请内容为空",
            "请说明要采购的物料、数量和到货仓库，例如「给一号仓补 8000 公斤冷轧钢板」。",
            "task_input 只给了菜单 id",
        )

    materials = state.get(SubPurchaseRequestStateField.MATERIALS) or []
    warehouses = state.get(SubPurchaseRequestStateField.WAREHOUSES) or []
    suppliers = state.get(SubPurchaseRequestStateField.SUPPLIERS) or []

    draft = llm_purchase_draft(text, materials, warehouses)

    material = _find_material(materials, draft.material_code)
    if material is None:
        return _failure(
            NodeName.MATCH.value, "没认出来是哪个物料",
            f"没有找到「{draft.material_query or text[:20]}」对应的物料，请提供物料编码（形如 M100023）。",
            f"物料主数据里没有 {draft.material_code or '（编码为空）'}",
        )

    if draft.qty <= 0:
        return _failure(
            NodeName.MATCH.value, "没有识别到数量",
            f"「{material.material_name}」这次要采购多少？请带上数量和单位（如 8000 公斤）。",
            "抽取结果 qty=0",
        )

    if not draft.target_warehouse:
        return _failure(
            NodeName.MATCH.value, "没有识别到到货仓库",
            f"「{material.material_name}」这笔货送到哪个仓库？（如 WH01 / 一号仓）",
            "抽取结果 target_warehouse 为空",
        )

    # ---- 取价：指定供应商优先，否则最低报价，兜底参考价（依据进审批卡）----
    quotes = _quotes_for(material.material_code)
    supplier, unit_price, avg_price, price_trace, pricing_error = _pick_supplier(
        material, draft.supplier_code, suppliers, quotes
    )
    if pricing_error:
        return _failure(
            NodeName.MATCH.value, "指定的供应商不存在",
            f"供应商主数据里没有「{draft.supplier_code}」，请核对供应商编码后重新提报。",
            pricing_error,
        )

    amount = round(draft.qty * unit_price, 2)
    lead_time = (
        int(supplier.lead_time_days)
        if supplier and supplier.lead_time_days > 0
        else _DEFAULT_LEAD_TIME_DAYS
    )
    expect_arrival = (datetime.now() + timedelta(days=lead_time)).strftime("%Y-%m-%d")

    # ---- 审批判定：策略引擎（data/seed/purchase_policy.json）----
    facts = _facts(material, draft.qty, draft.unit or material.unit,
                   unit_price, avg_price, amount, supplier)
    pm = match_policy(_POLICY_KIND, facts)
    if pm is None:
        # 判定不可用时拒绝自动放行：宁可按「需人工」处理，也不要把一次
        # 策略库故障静默演成「全部免审批」。
        return _failure(
            NodeName.MATCH.value, "审批策略不可用",
            "暂时无法判定这笔申请的审批路径，已转人工处理。",
            "策略库为空或没有兜底规则，拒绝自动放行",
        )
    need_approval = pm.need_approval

    operator_id = _operator_id(state)
    biz_id = build_request_id(
        material.material_code, draft.qty, draft.target_warehouse, operator_id
    )

    # 判定依据逐条外显：命中条件（✓）、被跳过的高优先级规则（✗）——
    # 「为什么这单要审、那单不用」的唯一证据，审批卡与演示输出都读它。
    trace = [f"✓ {line}" for line in price_trace + list(pm.trace)]
    trace += [f"✗ {line}" for line in pm.rejected]

    plan = PurchasePlan(
        plan_id=build_plan_id(biz_id, "purchase_request"),
        biz_id=biz_id,
        material_code=material.material_code,
        material_name=material.material_name,
        qty=draft.qty,
        unit=draft.unit or material.unit,
        unit_price=unit_price,
        amount=amount,
        target_warehouse=draft.target_warehouse,
        supplier_code=supplier.supplier_code if supplier else "",
        supplier_name=supplier.supplier_name if supplier else "",
        expect_arrival=expect_arrival,
        need_approval=need_approval,
        policy_id=pm.policy_id,
        policy_title=pm.title,
        priority=pm.priority,
        reason=pm.render_reason(),
        reason_trace=trace,
        status="pending_approval" if need_approval else "matched",
        operator_id=operator_id,
        description=" ".join(text.split()),
        message=(
            f"申请已命中「{pm.title}」，需人工审批后执行，正在等待审批（金额 {amount:.2f} 元）。"
            if need_approval
            else f"申请已受理，正在为您下达采购订单（金额 {amount:.2f} 元）。"
        ),
    )

    for line in trace:
        emit("policy_match", detail=line)

    node_done(
        NodeName.MATCH.value,
        f"{biz_id} amount={amount:.2f} policy={pm.policy_id} approval={need_approval}",
    )

    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: plan,
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.MATCH.value,
            execute_result_code=0,
            execute_result_msg=f"生成采购方案 {biz_id}（{pm.policy_id} {pm.title}）",
            execute_exceptions=[]
        )]
    }


def _find_material(materials, code: str) -> Optional[MaterialInfo]:
    if not code:
        return None
    for m in materials:
        if m.material_code == code:
            return m
    return None


# ---------------------------------------------------------------- 节点：审批

def approval_node(state: SubPurchaseRequestState, config: RunnableConfig):
    """人工审批。

    ⚠️ 这个节点在恢复时会**从第一行整体重放**，中断之前只能读状态、
    不能有任何副作用。所有写操作都在 `execute_node` 里。
    """
    plan = state.get(SubPurchaseRequestStateField.PURCHASE_REQUEST)
    if plan is None:  # pragma: no cover - 路由保证不会走到
        return _failure(NodeName.APPROVAL.value, "没有待审批的方案",
                        "审批失败，请重试。", "状态里没有 purchase_request")

    round_no = plan.revision + 1
    logger.info(f"=== 等待人工审批：{plan.plan_id}（第 {round_no} 轮）===")
    emit("hitl_wait", detail=(
        f"{plan.material_name}：{plan.amount:.2f} 元待审批"
        + (f"（第 {round_no} 轮）" if plan.revision else "")
    ))

    decision = interrupt({
        "type": "purchase_approval",
        "plan_id": plan.plan_id,
        "biz_id": plan.biz_id,
        "revision": plan.revision,
        "policy_id": plan.policy_id,
        "material_code": plan.material_code,
        "material_name": plan.material_name,
        "qty": plan.qty,
        "unit": plan.unit,
        "unit_price": plan.unit_price,
        "amount": plan.amount,
        "target_warehouse": plan.target_warehouse,
        "supplier_code": plan.supplier_code,
        "supplier_name": plan.supplier_name,
        "expect_arrival": plan.expect_arrival,
        "reason": plan.reason,
        "reason_trace": plan.reason_trace,
        "description": (
            f"{plan.policy_title}：{plan.material_name}（{plan.material_code}）"
            f"{plan.qty:g}{plan.unit}，金额 {plan.amount:.2f} 元，"
            f"请确认是否批准执行"
        ),
    })

    action, operator, patch = _parse_decision(decision)
    logger.info(f"审批结论：{_ACTION_TEXT[action]}（{operator or '未署名'}）")

    thread_id = _thread_id(config)
    guard = IdempotencyGuard()

    if action == "recreate":
        return _recreate_node(state, config, plan, patch, operator, guard)

    approved = action == "approve"
    # 审批记录同一轮内首写胜：这个节点会被重放，重放不应该记第二笔
    is_first = guard.record_approval(
        thread_id=thread_id, plan_id=plan.plan_id,
        decision="approved" if approved else "rejected",
        operator=operator, revision=plan.revision,
        biz_id=plan.biz_id, kind="purchase",
    )
    if not is_first:
        emit("idempotent_replay", detail=f"审批记录已存在，跳过重复写入（{plan.plan_id}）")

    updated = plan.model_copy(update={
        "status": "approved" if approved else "rejected",
        "approved_by": operator,
        "message": (
            f"审批通过，正在下达采购订单（金额 {plan.amount:.2f} 元）。" if approved
            else f"审批未通过，申请单 {plan.biz_id} 已驳回，将转人工说明。"
        ),
    })

    emit("policy_match", detail=f"审批{'通过' if approved else '驳回'}：{plan.plan_id}")

    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: updated,
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.APPROVAL.value,
            execute_result_code=0,
            execute_result_msg=f"人工审批{'通过' if approved else '驳回'}（{plan.plan_id}）",
            execute_exceptions=[]
        )]
    }


def _recreate_node(state: SubPurchaseRequestState, config: RunnableConfig,
                   plan: PurchasePlan, patch: Dict[str, Any], operator: str,
                   guard: IdempotencyGuard) -> Dict[str, Any]:
    """改判重报：按 patch 修改要素，**重走一遍策略引擎**。

    只有 patch 点名的字段会被改动，其余要素原样保持 —— 这正是「一次通过率」
    机制的意义：驳回带修改重报，而不是从头再提一遍。复判可能降为免审批
    （直接流转执行），也可能仍需审批（第二次中断，审批卡上看到的是改后的版本）。

    patch 是人工入口的自由输入：只认白名单字段，值不合法的条目原样忽略并留痕
    —— 宁可少改一个字段（人还能再改一轮），也不要因为一个笔误废掉整轮审批。
    """
    thread_id = _thread_id(config)
    guard.record_approval(
        thread_id=thread_id, plan_id=plan.plan_id,
        decision="recreated", operator=operator, revision=plan.revision,
        biz_id=plan.biz_id, kind="purchase",
    )

    materials = state.get(SubPurchaseRequestStateField.MATERIALS) or []
    suppliers = state.get(SubPurchaseRequestStateField.SUPPLIERS) or []
    material = _find_material(materials, plan.material_code)
    if material is None:  # pragma: no cover - 状态里方案来自 match，物料必在
        return _failure(NodeName.APPROVAL.value, "物料主数据缺失",
                        "重新提报失败，请重试。", "状态里没有该物料的物料主数据")

    updates, notes = _apply_recreate_patch(plan, patch)
    qty = float(updates.get("qty", plan.qty))
    warehouse = str(updates.get("target_warehouse", plan.target_warehouse))
    requested = str(updates.get("supplier_code", plan.supplier_code))

    quotes = _quotes_for(plan.material_code)
    supplier, unit_price, avg_price, price_trace, pricing_error = _pick_supplier(
        material, requested, suppliers, quotes
    )
    if pricing_error:
        return _failure(
            NodeName.APPROVAL.value, "修改后的供应商不存在",
            f"供应商主数据里没有「{requested}」，本次修改未生效，请核对后重新审批。",
            pricing_error,
        )

    amount = round(qty * unit_price, 2)
    lead_time = (
        int(supplier.lead_time_days)
        if supplier and supplier.lead_time_days > 0
        else _DEFAULT_LEAD_TIME_DAYS
    )
    expect_arrival = (datetime.now() + timedelta(days=lead_time)).strftime("%Y-%m-%d")

    facts = _facts(material, qty, plan.unit or material.unit,
                   unit_price, avg_price, amount, supplier)
    pm = match_policy(_POLICY_KIND, facts)
    if pm is None:  # pragma: no cover - match 节点已拦过一次
        return _failure(NodeName.APPROVAL.value, "审批策略不可用",
                        "暂时无法复判这笔申请，已转人工处理。",
                        "策略库为空或没有兜底规则，拒绝自动放行")

    need_approval = pm.need_approval
    revision = plan.revision + 1

    # 数量 / 到货仓变了就是另一笔申请：单号（幂等锚点）必须跟着换，
    # 否则两笔要素不同的申请会共用一个幂等键，执行时被判成「内容冲突」。
    biz_id, plan_id = plan.biz_id, plan.plan_id
    if qty != plan.qty or warehouse != plan.target_warehouse:
        biz_id = build_request_id(material.material_code, qty, warehouse, plan.operator_id)
        plan_id = build_plan_id(biz_id, "purchase_request")

    trace = [f"↻ 第 {revision + 1} 轮重报：{'；'.join(notes) if notes else '未带修改，原样复判'}"]
    trace += [f"✓ {line}" for line in price_trace + list(pm.trace)]
    trace += [f"✗ {line}" for line in pm.rejected]

    updated = plan.model_copy(update={
        "plan_id": plan_id,
        "biz_id": biz_id,
        "revision": revision,
        "qty": qty,
        "unit_price": unit_price,
        "amount": amount,
        "target_warehouse": warehouse,
        "supplier_code": supplier.supplier_code if supplier else "",
        "supplier_name": supplier.supplier_name if supplier else "",
        "expect_arrival": expect_arrival,
        "need_approval": need_approval,
        "policy_id": pm.policy_id,
        "policy_title": pm.title,
        "priority": pm.priority,
        "reason": pm.render_reason(),
        "reason_trace": trace,
        "status": "pending_approval" if need_approval else "matched",
        "message": (
            f"已按修改重新提报（第 {revision + 1} 轮），策略复判后仍需审批，正在等待审批。"
            if need_approval
            else f"已按修改重新提报（第 {revision + 1} 轮），策略复判后免审批，正在下达采购订单。"
        ),
    })

    emit("hitl_recreate", detail=(
        f"{plan.policy_id} → {pm.policy_id}（{plan.policy_title} → {pm.title}），"
        f"第 {revision + 1} 轮"
    ))

    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: updated,
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.APPROVAL.value,
            execute_result_code=0,
            execute_result_msg=(
                f"人工改判重报（第 {revision + 1} 轮：{plan.policy_id} → {pm.policy_id}）"
            ),
            execute_exceptions=[]
        )]
    }


def _apply_recreate_patch(plan: PurchasePlan, patch: Any) -> Tuple[Dict[str, Any], List[str]]:
    """从 recreate 的 patch 里挑出允许修改的字段，其余原样忽略并留痕。

    校验失败的行必须出现在轨迹里 —— 「我明明改了数量，怎么还是按原数量重报的」
    这类疑问只有留痕才回答得了。空 plan 的未用参数保留签名对称，便于日后扩展
    （例如按策略白名单让不同规则允许改不同字段）。
    """
    del plan  # 目前校验与方案内容无关；保留参数是为了签名稳定
    updates: Dict[str, Any] = {}
    notes: List[str] = []
    source = patch if isinstance(patch, dict) else {}

    for key in sorted(source):
        if key not in _PATCH_FIELDS:
            notes.append(f"{key} 不是可修改字段，已忽略")

    if "supplier_code" in source:
        code = str(source["supplier_code"] or "").strip().upper()
        if code:
            updates["supplier_code"] = code
            notes.append(f"供应商改为 {code}")
        else:
            notes.append("supplier_code 为空，已忽略")

    if "qty" in source:
        try:
            qty = float(source["qty"])
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0:
            updates["qty"] = qty
            notes.append(f"数量改为 {qty:g}")
        else:
            notes.append(f"qty「{source['qty']}」不是有效数量，已忽略")

    if "target_warehouse" in source:
        wh = str(source["target_warehouse"] or "").strip().upper()
        if wh:
            updates["target_warehouse"] = wh
            notes.append(f"到货仓改为 {wh}")
        else:
            notes.append("target_warehouse 为空，已忽略")

    return updates, notes


def _parse_decision(decision: Any) -> Tuple[str, str, Dict[str, Any]]:
    """审批入参归一：把各调用方的写法收敛成一个动作 + 署名 + 可选 patch。

    四种形态都要认（与收货复核链路同一份协议，不另立一套）：
      * `{"confirmed": "ok"|"discard"|"recreate", "patch": {...}}` —— 图内 HITL 协议
        （demo runner 与 SSE 恢复用这个）
      * `{"decision": "recreate", "patch": {...}}` —— 后台审批接口的结构化写法
      * `{"approved": True, "operator": "OP002"}` —— 旧版结构化写法
      * `"ok"` —— 交互式终端直接给的字符串

    ⚠️ **必须先认 `confirmed`**：`interrupt()` 会把 resume 的值原样返回，
    而 demo runner 包的是 `{"confirmed": "ok"}`。只认 `approved` 的话，
    一次「通过」会被判成「驳回」——而且日志、事件、结果码全都是正常的，
    只有审批结论是反的，是最难从现象倒推回来的那类 bug。

    认不出的取值一律按驳回处理（拒绝执行是安全方向）；`patch` 只在 recreate
    分支被读取，其余分支原样忽略。
    """
    if isinstance(decision, dict):
        operator = str(decision.get("operator", "") or "")
        patch = decision.get("patch")
        patch = dict(patch) if isinstance(patch, dict) else {}

        raw = decision.get(_CONFIRMED)
        if raw is None:
            raw = decision.get("decision")
        if raw is not None:
            return _classify(str(raw)), operator, patch

        approved = decision.get("approved")
        if approved is None:
            return "discard", operator, patch
        return ("approve" if bool(approved) else "discard"), operator, patch

    return _classify(str(decision or "")), "", {}


def _classify(raw: str) -> str:
    value = raw.strip().lower()
    if value in _APPROVE_VALUES:
        return "approve"
    if value in _RECREATE_VALUES:
        return "recreate"
    return "discard"


#: 图内 HITL 协议的字段名，与其他 HITL 子图共用（恢复值会被原样返回）
_CONFIRMED = "confirmed"

#: 视为「通过」的取值（覆盖终端、后台接口、SSE 三种调用方的写法）
_APPROVE_VALUES = {"ok", "approve", "approved", "yes", "pass", "通过"}

#: 视为「改判重报」的取值
_RECREATE_VALUES = {"recreate", "reapply", "revise", "重报", "改报"}

#: 动作 -> 中文（日志用）
_ACTION_TEXT = {"approve": "通过", "discard": "驳回", "recreate": "改判重报"}


def _thread_id(config: RunnableConfig) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id", "") or "")


# ---------------------------------------------------------------- 节点：执行

def execute_node(state: SubPurchaseRequestState, config: RunnableConfig):
    """执行方案。**这是唯一有副作用的节点**，也是幂等保护落点。"""
    plan = state.get(SubPurchaseRequestStateField.PURCHASE_REQUEST)
    if plan is None:  # pragma: no cover
        return _failure(NodeName.EXECUTE.value, "没有可执行的方案",
                        "执行失败，请重试。", "状态里没有 purchase_request")

    if plan.status == "rejected":
        logger.info(f"方案 {plan.plan_id} 已被驳回，不执行")
        # 这个节点没有 emit 时，runner 会按 NODE_EVENTS 兜底补一条
        # 「采购订单已下达」——驳回的单子上顶着这条时间线是误导。
        emit("hitl_rejected", detail=f"{plan.biz_id} 审批驳回，未下单")
        return _done(plan, "审批驳回，未执行任何写操作")

    guard = IdempotencyGuard()
    payload = {
        "biz_id": plan.biz_id,
        "material_code": plan.material_code,
        "qty": plan.qty,
        "target_warehouse": plan.target_warehouse,
        "supplier_code": plan.supplier_code,
        "amount": plan.amount,
    }
    key = build_key(plan.biz_id, "purchase_request")

    try:
        record = guard.begin(
            order_id=plan.biz_id, action_type="purchase_request", payload=payload
        )
    except IdempotencyConflict as conflict:
        # 同一个单号但要素不同：不猜、不静默复用，交给人
        logger.error(f"幂等冲突：{conflict}")
        emit("idempotent_conflict", detail=f"{conflict.key} 的要素与首次不一致")
        updated = plan.model_copy(update={
            "status": "conflict",
            "message": "这笔申请此前已提交过、且要素不一致，为避免重复下单，已转人工核实。",
        })
        return _done(updated, "幂等冲突，转人工核实")

    if record.status == "done":
        # 短路：不再调下游，直接把首次的结果返回去
        logger.info(f"幂等命中，短路执行：{key}")
        emit("idempotent_replay", detail=f"命中幂等记录 {key}，未重复下单")
        replayed = plan.model_copy(update={
            "status": "replayed",
            "replayed": True,
            "po_no": str((record.result or {}).get("po_no", "") or ""),
            "message": (
                f"这笔申请之前已经下过单了（采购订单 {record.result.get('po_no', '—')}），"
                f"本次没有重复提交。"
            ),
        })
        return _done(replayed, "命中幂等，未重复执行")

    # ---- 真正执行 ----
    try:
        result = _submit(plan)
    except Exception as e:
        guard.fail(key, str(e))
        logger.error(f"采购下单失败：{e}")
        return _failure(NodeName.EXECUTE.value, "下单失败",
                        "提交采购订单时接口异常，已转人工跟进。", str(e))

    guard.complete(key, result)
    emit("purchase_submitted", detail=f"采购订单已下达（{result.get('po_no', '—')}）")

    updated = plan.model_copy(update={
        "status": "executed",
        "po_no": str(result.get("po_no") or ""),
        "message": _executed_message(plan, result),
    })
    return _done(updated, f"已执行（采购订单 {result.get('po_no', '—')}）")


def _submit(plan: PurchasePlan) -> Dict[str, Any]:
    """提交采购订单到 ERP（经采购端口）。

    上游报文的差异（poNo / totalAmt 分 → 内部口径）由 normalize 收口，
    执行节点与文案只认 po_no / status / amount —— 与装载链路同一套归一，
    不在这里再造一份字段映射。

    幂等键与本地幂等守卫用同一个 key：守卫管「同一笔业务不重复执行」，
    上游那串 key 管「网络重试、并发请求到了 ERP 也不重复下单」。
    """
    record = get_purchase_port().create_purchase_order({
        "material_code": plan.material_code,
        "supplier_code": plan.supplier_code,
        "request_id": plan.biz_id,
        "qty": plan.qty,
        "unit": plan.unit,
        "unit_price": plan.unit_price,
        "amount": plan.amount,
        "target_warehouse": plan.target_warehouse,
        "operator_id": plan.operator_id,
        "idempotency_key": build_key(plan.biz_id, "purchase_request"),
    })

    order = normalize_purchase_order(record)
    return {
        "po_no": order["po_no"],
        "status": order["status"],
        "amount": order["amount"],
    }


def _executed_message(plan: PurchasePlan, result: Dict[str, Any]) -> str:
    number = result.get("po_no") or ""
    return (
        f"采购订单已下达{'（单号 ' + number + '）' if number else ''}，"
        f"预计 {plan.expect_arrival or '按供应商交期'} 到货，到货后请及时登记收货。"
    )


# ---------------------------------------------------------------- 返回封装

def _done(plan: PurchasePlan, note: str) -> Dict[str, Any]:
    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: plan,
        SubPurchaseRequestStateField.TASK_RESULT: TaskResult(
            task_result=plan.render(),
            task_result_explain=f"{plan.policy_id}：{note}",
            # 驳回、命中幂等、冲突都是**正常业务结论**，不是失败。
            # 判成失败会让上层把正确的兜底当 bug。
            task_result_code=0
        ),
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.EXECUTE.value,
            execute_result_code=0,
            execute_result_msg=note,
            execute_exceptions=[]
        )]
    }


def _failure(node: str, msg: str, reply: str, detail: str) -> Dict[str, Any]:
    """要素缺失 / 策略不可用等「没有形成可执行方案」的返回。

    status 用 `failed` 而不是 `matched`：子图的条件边会把它直接送到 END ——
    失败方案绝不能流过执行节点（那会拿空要素去下单）。这条曾经真的发生过：
    status=matched 的失败方案在条件边眼里和「免审批方案」一模一样。
    """
    logger.warning(f"{msg}：{detail}")
    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: PurchasePlan(
            status="failed", policy_title=msg, message=reply, reason=detail,
        ),
        SubPurchaseRequestStateField.TASK_RESULT: TaskResult(
            task_result=reply, task_result_explain=detail, task_result_code=1
        ),
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=node,
            execute_result_code=1,
            execute_result_msg=msg,
            execute_exceptions=[detail]
        )]
    }
