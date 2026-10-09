"""
收货验收子图的节点。

链路：欢迎 → 要素提取 + 质检判定 →（不良率超阈值）人工复核 →（可改判重报）→ 执行 + 幂等记账。

与采购申请同一条 HITL × 幂等骨架（四条不变量见 `sub_purchase_request_node`），
差别只在触发条件与复核对象：

  * 采购看**金额**（要不要花这笔钱），收货看**不良率**（这批货能不能收）。
  * 复核的产物是处置方式：入库 / 让步接收 / 退货 / 换货。复核人能用
    recreate+patch 在复审时改处置 —— patch 只放行 `disposition` 一个字段，
    因为实收多少、不良多少是**到货事实**（单据数据），能改它的只有登记环节。

不良率口径只有一处
------------------
`ReceiptPlan.compute_defect_rate` 是唯一计算处：阈值判定、方案里的数字、
给人看的文案读的都是同一个值。若各写一份 `defect / received`，
演示时「判定说超标、页面显示没超」这种自相矛盾迟早会出现。

复核判定（Phase 5 全量）
------------------------
阈值不再写死，走 `integrations/policy.py` 读 `data/seed/quality_policy.json`：
QC-001（不良率 > 2% 需复核）/ QC-000（兜底免复核），逐条进理由轨迹。

patch 改不了「不良率」这个事实，所以收货侧的 recreate 复判**必然仍命中 QC-001**
—— 二次中断是正常路径而不是死循环：第二轮复核卡上带着改后的处置方式，
复核人看到的就是最终要执行的方案（approve 分支保留 patch 后的处置）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.sub_goods_receipt.sub_goods_receipt_state import (
    SubGoodsReceiptState,
    SubGoodsReceiptStateField,
)
from integrations.factory import get_receipt_port
from integrations.idempotency import (
    IdempotencyConflict,
    IdempotencyGuard,
    build_key,
    build_plan_id,
    build_receipt_id,
)
from integrations.normalize import normalize_receipt
from integrations.policy import PolicyFacts, match as match_policy
from llm.llm_receipt_draft import llm_receipt_draft
from models.scm_models import NodeResult, PurchaseOrderInfo, ReceiptPlan, TaskResult
from observability.progress import emit, node_done
from utils.agent_logger import get_logger

logger = get_logger("sub_goods_receipt_node")

#: 质检策略文件名（规则引擎按这个去 data/seed/ 取）
_POLICY_KIND = "quality_policy"

#: 复核可落定的处置方式。也是 recreate 的 patch 允许写入的取值域 ——
#: 「待复核」是中间态，不在其中（第一轮批准时按让步接收落定）。
_DISPOSITIONS = ("入库", "让步接收", "退货", "换货")


class NodeName(str, Enum):
    WELCOME  = "goods_receipt_welcome_node"
    MATCH    = "goods_receipt_match_node"
    APPROVAL = "goods_receipt_approval_node"
    EXECUTE  = "goods_receipt_execute_node"


# ---------------------------------------------------------------- 输入定位

def _receipt_text(state: SubGoodsReceiptState) -> str:
    """这次要处理的登记原文；菜单 id 直达时为空（如实提示补充，不硬编）。"""
    raw = (state.get(SubGoodsReceiptStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.GOODS_RECEIPT.value:
        return raw
    return ""


def _operator_id(state: SubGoodsReceiptState) -> str:
    operator = state.get(SubGoodsReceiptStateField.OPERATOR_INFO)
    return getattr(operator, "operator_id", "") or ""


def _find_order(orders, po_no: str) -> Optional[PurchaseOrderInfo]:
    if not po_no:
        return None
    for o in orders:
        if o.po_no == po_no:
            return o
    return None


def _quality_facts(material_code: str, material_name: str, qty_received: int,
                   qty_defect: int, defect_rate: float) -> PolicyFacts:
    """装配质检策略引擎的输入事实（只填收货侧字段，采购侧留默认）。

    规则引擎的输入越窄越好：这批到货的「事实」就是实收、不良、不良率三个数，
    其余上下文不该影响判定（见 `policy.PolicyFacts`）。
    """
    return PolicyFacts(
        material_code=material_code,
        material_name=material_name,
        qty_received=qty_received,
        qty_defect=qty_defect,
        defect_rate=defect_rate,
    )


# ---------------------------------------------------------------- 节点：定方案

def welcome_node(state: SubGoodsReceiptState, config: RunnableConfig):
    """进要素提取之前先报一声。"""
    logger.info("=== 收货验收 ===")
    emit("quality_check", detail="准备提取收货登记要素")
    return {
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME.value,
            execute_result_code=0,
            execute_result_msg="正在登记到货信息并做质检判定，请稍等。",
            execute_exceptions=[]
        )]
    }


def match_node(state: SubGoodsReceiptState, config: RunnableConfig):
    """抽要素 → 质检判定 → 出方案。**不产生任何副作用。**"""
    logger.info("=== 收货登记要素提取 ===")

    text = _receipt_text(state)
    if not text:
        return _failure(
            NodeName.MATCH.value, "登记内容为空",
            "请说明采购订单号、实收数量和不良数量，例如「PO20260928002 到货 920 支，磕碰变形 30 支」。",
            "task_input 只给了菜单 id",
        )

    materials = state.get(SubGoodsReceiptStateField.MATERIALS) or []
    warehouses = state.get(SubGoodsReceiptStateField.WAREHOUSES) or []
    orders = state.get(SubGoodsReceiptStateField.PURCHASE_ORDERS) or []

    draft = llm_receipt_draft(text, materials, warehouses, orders)

    order = _find_order(orders, draft.po_no)
    if order is None:
        return _failure(
            NodeName.MATCH.value, "没认出来是哪张采购订单",
            "没有找到这笔到货对应的采购订单，请提供订单号（形如 PO20260928002）。",
            f"采购订单里没有 {draft.po_no or '（单号为空）'}",
        )

    if draft.qty_received <= 0:
        return _failure(
            NodeName.MATCH.value, "没有识别到实收数量",
            f"「{order.material_code}」这批到货实收多少？请带上数量（如 920 支）。",
            "抽取结果 qty_received=0",
        )

    qty_defect = draft.qty_defect
    if qty_defect < 0:
        qty_defect = 0
    if qty_defect > draft.qty_received:
        # 不良数大于实收数是不合逻辑的登记，退回让人改 —— 不替用户「纠正」
        return _failure(
            NodeName.MATCH.value, "登记数量不合逻辑",
            f"不良数量（{qty_defect}）不能大于实收数量（{draft.qty_received}），请核对后重新登记。",
            f"qty_defect={qty_defect} > qty_received={draft.qty_received}",
        )

    material = next((m for m in materials if m.material_code == order.material_code), None)
    material_name = material.material_name if material else order.material_code

    defect_rate = ReceiptPlan.compute_defect_rate(draft.qty_received, qty_defect)

    # ---- 复核判定：策略引擎（data/seed/quality_policy.json）----
    facts = _quality_facts(order.material_code, material_name,
                           draft.qty_received, qty_defect, defect_rate)
    pm = match_policy(_POLICY_KIND, facts)
    if pm is None:
        # 判定不可用时拒绝自动放行：宁可按「需人工」处理，也不要把一次
        # 策略库故障静默演成「全部免复核」。
        return _failure(
            NodeName.MATCH.value, "质检策略不可用",
            "暂时无法判定这批到货的复核路径，已转人工处理。",
            "策略库为空或没有兜底规则，拒绝自动放行",
        )
    need_review = pm.need_approval
    disposition = "待复核" if need_review else "入库"

    # 判定依据逐条外显：命中条件（✓）、被跳过的高优先级规则（✗）
    trace = [f"✓ {line}" for line in pm.trace]
    trace += [f"✗ {line}" for line in pm.rejected]

    operator_id = _operator_id(state)
    biz_id = build_receipt_id(order.po_no, draft.qty_received, qty_defect)

    plan = ReceiptPlan(
        plan_id=build_plan_id(biz_id, "goods_receipt"),
        biz_id=biz_id,
        po_no=order.po_no,
        material_code=order.material_code,
        material_name=material_name,
        warehouse_code=draft.warehouse_code or order.target_warehouse,
        supplier_code=order.supplier_code,
        qty_expected=int(order.qty),
        qty_received=draft.qty_received,
        qty_defect=qty_defect,
        defect_rate=defect_rate,
        defect_type=draft.defect_type,
        disposition=disposition,
        need_review=need_review,
        policy_id=pm.policy_id,
        policy_title=pm.title,
        priority=pm.priority,
        reason=pm.render_reason(),
        reason_trace=trace,
        status="pending_review" if need_review else "matched",
        inspector=operator_id,
        message=(
            f"不良率 {defect_rate:.2%} 超过阈值，需人工复核后决定处置方式，正在等待复核。"
            if need_review
            else f"到货验收合格（不良率 {defect_rate:.2%}），已办理入库。"
        ),
    )

    for line in trace:
        emit("quality_check", detail=line)

    node_done(
        NodeName.MATCH.value,
        f"{biz_id} defect_rate={defect_rate:.2%} policy={pm.policy_id} review={need_review}",
    )

    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: plan,
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.MATCH.value,
            execute_result_code=0,
            execute_result_msg=f"生成收货方案 {biz_id}（{pm.policy_id} {pm.title}）",
            execute_exceptions=[]
        )]
    }


# ---------------------------------------------------------------- 节点：复核

def approval_node(state: SubGoodsReceiptState, config: RunnableConfig):
    """质检复核。

    ⚠️ 恢复时从第一行整体重放，中断之前只能读状态、不能有副作用。
    """
    plan = state.get(SubGoodsReceiptStateField.GOODS_RECEIPT)
    if plan is None:  # pragma: no cover - 路由保证不会走到
        return _failure(NodeName.APPROVAL.value, "没有待复核的方案",
                        "复核失败，请重试。", "状态里没有 goods_receipt")

    round_no = plan.revision + 1
    logger.info(f"=== 等待质检复核：{plan.plan_id}（第 {round_no} 轮）===")
    emit("hitl_wait", detail=(
        f"{plan.material_name}：不良率 {plan.defect_rate:.2%} 待复核"
        + (f"（第 {round_no} 轮）" if plan.revision else "")
    ))

    decision = interrupt({
        "type": "quality_review",
        "plan_id": plan.plan_id,
        "biz_id": plan.biz_id,
        "revision": plan.revision,
        "po_no": plan.po_no,
        "material_code": plan.material_code,
        "material_name": plan.material_name,
        "warehouse_code": plan.warehouse_code,
        "supplier_code": plan.supplier_code,
        "qty_expected": plan.qty_expected,
        "qty_received": plan.qty_received,
        "qty_defect": plan.qty_defect,
        "defect_rate": plan.defect_rate,
        "defect_type": plan.defect_type,
        "disposition": plan.disposition,
        "policy_id": plan.policy_id,
        "reason": plan.reason,
        "reason_trace": plan.reason_trace,
        "description": (
            f"{plan.policy_title}：{plan.material_name}（{plan.material_code}）"
            f"实收 {plan.qty_received}，不良 {plan.qty_defect}（{plan.defect_rate:.2%}），"
            f"请确认处置方式"
        ),
    })

    action, operator, patch = _parse_decision(decision)
    logger.info(f"复核结论：{_ACTION_TEXT[action]}（{operator or '未署名'}）")

    thread_id = _thread_id(config)
    guard = IdempotencyGuard()

    if action == "recreate":
        return _recreate_node(state, config, plan, patch, operator, guard)

    approved = action == "approve"
    # 复核记录同一轮内首写胜：这个节点会被重放，重放不应该记第二笔
    is_first = guard.record_approval(
        thread_id=thread_id, plan_id=plan.plan_id,
        decision="approved" if approved else "rejected",
        operator=operator, revision=plan.revision,
    )
    if not is_first:
        emit("idempotent_replay", detail=f"复核记录已存在，跳过重复写入（{plan.plan_id}）")

    if approved:
        # 复审轮次里 patch 已经把处置方式写进了 plan.disposition，复核通过就按
        # 它执行；第一轮批准时 disposition 还是「待复核」，按让步接收落定
        # （整批处理：良品入库、不良品按协议处理）。
        disposition = plan.disposition if plan.disposition in _DISPOSITIONS else "让步接收"
        message = f"复核通过，按「{disposition}」处置执行。"
    else:
        disposition = "退货"
        message = f"复核未通过：整批拒收，{plan.qty_received} 件按退货处理。"

    updated = plan.model_copy(update={
        "status": "approved" if approved else "rejected",
        "approved_by": operator,
        "disposition": disposition,
        "message": message,
    })

    emit("quality_check", detail=f"复核{'通过' if approved else '驳回'}：{plan.plan_id}")

    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: updated,
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.APPROVAL.value,
            execute_result_code=0,
            execute_result_msg=f"质检复核{'通过' if approved else '驳回'}（{plan.plan_id}）",
            execute_exceptions=[]
        )]
    }


def _recreate_node(state: SubGoodsReceiptState, config: RunnableConfig,
                   plan: ReceiptPlan, patch: Dict[str, Any], operator: str,
                   guard: IdempotencyGuard) -> Dict[str, Any]:
    """改判重报：按 patch 改处置方式，**重走一遍策略引擎**。

    patch 只有 `disposition` 一个字段会生效：实收/不良是到货事实（登记环节
    写下的数据），复核人不能改事实，只能改对事实的处置。复判时不良率没变、
    QC-001 必然仍命中 —— 二次中断是正常路径：第二轮复核卡上带着改后的处置
    方式，复核人看到的就是最终要执行的方案。

    不带改动直接重报（patch 缺席或值不合法）同样走这条路：原样复判、
    再中断一轮，绝不静默放行。
    """
    thread_id = _thread_id(config)
    guard.record_approval(
        thread_id=thread_id, plan_id=plan.plan_id,
        decision="recreated", operator=operator, revision=plan.revision,
    )

    disposition, notes = _apply_recreate_patch(patch)

    facts = _quality_facts(plan.material_code, plan.material_name,
                           plan.qty_received, plan.qty_defect, plan.defect_rate)
    pm = match_policy(_POLICY_KIND, facts)
    if pm is None:  # pragma: no cover - match 节点已拦过一次
        return _failure(NodeName.APPROVAL.value, "质检策略不可用",
                        "暂时无法复判这批到货，已转人工处理。",
                        "策略库为空或没有兜底规则，拒绝自动放行")

    need_review = pm.need_approval
    revision = plan.revision + 1

    trace = [f"↻ 第 {revision + 1} 轮重报：{'；'.join(notes) if notes else '未带修改，原样复判'}"]
    trace += [f"✓ {line}" for line in pm.trace]
    trace += [f"✗ {line}" for line in pm.rejected]

    updated = plan.model_copy(update={
        "revision": revision,
        "disposition": disposition or plan.disposition,
        "need_review": need_review,
        "policy_id": pm.policy_id,
        "policy_title": pm.title,
        "priority": pm.priority,
        "reason": pm.render_reason(),
        "reason_trace": trace,
        "status": "pending_review" if need_review else "matched",
        "message": (
            f"已按修改重新提报（第 {revision + 1} 轮），复判仍需质检复核，正在等待复核。"
            if need_review
            else f"已按修改重新提报（第 {revision + 1} 轮），复判免复核，正在执行。"
        ),
    })

    emit("hitl_recreate", detail=(
        f"第 {revision + 1} 轮重报：{'；'.join(notes) if notes else '未带修改'}"
        + ("，复判仍需复核" if need_review else "，复判免复核")
    ))

    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: updated,
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.APPROVAL.value,
            execute_result_code=0,
            execute_result_msg=(
                f"人工改判重报（第 {revision + 1} 轮：处置「{disposition or plan.disposition}」）"
            ),
            execute_exceptions=[]
        )]
    }


def _apply_recreate_patch(patch: Any) -> Tuple[Optional[str], List[str]]:
    """从 recreate 的 patch 里取出允许修改的处置方式；其余键原样忽略并留痕。

    校验失败的行必须出现在轨迹里 —— 「我明明改成了换货，怎么还是按原处置
    走」这类疑问只有留痕才回答得了。
    """
    notes: List[str] = []
    source = patch if isinstance(patch, dict) else {}

    for key in sorted(source):
        if key != "disposition":
            notes.append(f"{key} 不是可修改字段，已忽略")

    if "disposition" not in source:
        return None, notes

    value = str(source["disposition"] or "").strip()
    if value in _DISPOSITIONS:
        notes.append(f"处置方式改为「{value}」")
        return value, notes

    notes.append(
        f"disposition「{value}」不在允许范围（{'/'.join(_DISPOSITIONS)}），已忽略"
    )
    return None, notes


def _parse_decision(decision: Any) -> Tuple[str, str, Dict[str, Any]]:
    """复核入参归一 —— 与采购审批同一份协议、同一套形态（见那边的注释）。

    返回 (动作, 署名, patch)：动作 ∈ approve / discard / recreate。
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

#: 视为「通过」的取值
_APPROVE_VALUES = {"ok", "approve", "approved", "yes", "pass", "通过"}

#: 视为「改判重报」的取值
_RECREATE_VALUES = {"recreate", "reapply", "revise", "重报", "改报"}

#: 动作 -> 中文（日志用）
_ACTION_TEXT = {"approve": "通过", "discard": "驳回", "recreate": "改判重报"}


def _thread_id(config: RunnableConfig) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id", "") or "")


# ---------------------------------------------------------------- 节点：执行

def execute_node(state: SubGoodsReceiptState, config: RunnableConfig):
    """执行方案。**唯一有副作用的节点**，也是幂等保护落点。"""
    plan = state.get(SubGoodsReceiptStateField.GOODS_RECEIPT)
    if plan is None:  # pragma: no cover
        return _failure(NodeName.EXECUTE.value, "没有可执行的方案",
                        "执行失败，请重试。", "状态里没有 goods_receipt")

    if plan.status == "rejected":
        logger.info(f"方案 {plan.plan_id} 复核未通过，按退货处理")
        # 见采购侧同款注释：不 emit 的话兜底事件会把驳回的登记标记成「已入库」
        emit("hitl_rejected", detail=f"{plan.biz_id} 复核驳回，未办理入库")
        return _done(plan, "复核驳回：整批退货，未办理入库")

    guard = IdempotencyGuard()
    payload = {
        "biz_id": plan.biz_id,
        "po_no": plan.po_no,
        "qty_received": plan.qty_received,
        "qty_defect": plan.qty_defect,
        "disposition": plan.disposition,
    }
    key = build_key(plan.biz_id, "goods_receipt")

    try:
        record = guard.begin(
            order_id=plan.biz_id, action_type="goods_receipt", payload=payload
        )
    except IdempotencyConflict as conflict:
        logger.error(f"幂等冲突：{conflict}")
        emit("idempotent_conflict", detail=f"{conflict.key} 的登记要素与首次不一致")
        updated = plan.model_copy(update={
            "status": "conflict",
            "message": "这笔到货此前已登记过、且登记要素不一致，为避免重复入库，已转人工核实。",
        })
        return _done(updated, "幂等冲突，转人工核实")

    if record.status == "done":
        logger.info(f"幂等命中，短路执行：{key}")
        emit("idempotent_replay", detail=f"命中幂等记录 {key}，未重复入库")
        replayed = plan.model_copy(update={
            "status": "replayed",
            "replayed": True,
            # 回填首次执行的正式单号，最终展示与首次执行逐字一致
            "receipt_no": str((record.result or {}).get("receipt_no", "") or ""),
            "message": (
                f"这笔到货之前已经登记过了（收货单 {record.result.get('receipt_no', plan.biz_id)}），"
                f"本次没有重复入库。"
            ),
        })
        return _done(replayed, "命中幂等，未重复执行")

    # ---- 真正执行 ----
    try:
        result = _submit(plan)
    except Exception as e:
        guard.fail(key, str(e))
        logger.error(f"收货入库失败：{e}")
        return _failure(NodeName.EXECUTE.value, "入库失败",
                        "提交收货单时接口异常，已转人工跟进。", str(e))

    guard.complete(key, result)
    emit("receipt_submitted", detail=f"收货单已提交（{result.get('receipt_no', '—')}）")

    updated = plan.model_copy(update={
        "status": "executed",
        # 正式单号回填进方案（与电商侧 refund_id 同一手法）：
        # 最终 render 的「收货单号」与实际登记到 WMS 的号只差这一步回填
        "receipt_no": str(result.get("receipt_no") or ""),
        "message": _executed_message(plan, result),
    })
    return _done(updated, f"已执行（{plan.disposition}）")


def _submit(plan: ReceiptPlan) -> Dict[str, Any]:
    """把收货结果提交下游（WMS 收货登记 + 质检报告归档），经收货端口。

    上游报文的差异（rcvNo / qtyActual / reportUrl）由 normalize 收口成内部口径，
    `execute_node` 与 `_executed_message` 只认 receipt_no / qty_stored /
    qty_returned —— 与装载链路走同一套归一，不在这里再造一份映射。

    幂等键与本地幂等守卫用同一个 key：守卫管「同一笔业务不重复执行」，
    上游那串 key 管「网络重试、并发请求到了 WMS 也不重复登记」。
    """
    record = get_receipt_port().submit_receipt({
        "po_no": plan.po_no,
        "qty_received": plan.qty_received,
        "qty_defect": plan.qty_defect,
        "defect_type": plan.defect_type,
        "disposition": plan.disposition,
        "warehouse_code": plan.warehouse_code,
        "inspector": plan.inspector,
        "idempotency_key": build_key(plan.biz_id, "goods_receipt"),
    })

    receipt = normalize_receipt(record)
    return {
        # 收货单号以上游（WMS）为准；上游没给才退回确定性单号
        "receipt_no": receipt["receipt_no"] or plan.biz_id,
        "disposition": plan.disposition,
        "qty_stored": max(plan.qty_received - plan.qty_defect, 0),
        "qty_returned": plan.qty_defect,
        "report_url": receipt["report_url"],
    }


def _executed_message(plan: ReceiptPlan, result: Dict[str, Any]) -> str:
    """执行结论文案：按**最终处置方式**分支（它可能来自复核、也可能来自 patch）。"""
    number = result.get("receipt_no") or plan.biz_id
    stored = result.get("qty_stored", 0)
    returned = result.get("qty_returned", 0)

    if plan.disposition == "退货":
        return f"已办理退货（收货单 {number}）：{plan.qty_received} 件整批退回供应商，未办理入库。"
    if plan.disposition == "换货":
        return (
            f"已办理换货（收货单 {number}）：{plan.qty_received} 件退回供应商换新，"
            f"请在新批次到货后重新登记收货。"
        )
    if plan.disposition == "让步接收":
        return (
            f"已按让步接收办理（收货单 {number}）：{stored} 件入库，"
            f"{returned} 件不良按协议折价处理。"
        )
    return (
        f"已办理入库 {stored} 件"
        f"{'，' + str(returned) + ' 件不良退回供应商' if returned else ''}"
        f"（收货单 {number}）。"
    )


# ---------------------------------------------------------------- 返回封装

def _done(plan: ReceiptPlan, note: str) -> Dict[str, Any]:
    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: plan,
        SubGoodsReceiptStateField.TASK_RESULT: TaskResult(
            task_result=plan.render(),
            task_result_explain=f"{plan.policy_id}：{note}",
            # 复核驳回、命中幂等、冲突都是正常业务结论，不是失败
            task_result_code=0
        ),
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.EXECUTE.value,
            execute_result_code=0,
            execute_result_msg=note,
            execute_exceptions=[]
        )]
    }


def _failure(node: str, msg: str, reply: str, detail: str) -> Dict[str, Any]:
    """要素缺失 / 策略不可用等「没有形成可执行方案」的返回。

    status 用 `failed` 而不是 `matched`：子图的条件边会把它直接送到 END ——
    失败方案绝不能流过执行节点（那会拿空要素去登记入库）。这条曾经真的发生过：
    status=matched 的失败方案在条件边眼里和「免复核方案」一模一样。
    """
    logger.warning(f"{msg}：{detail}")
    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: ReceiptPlan(
            status="failed", policy_title=msg, message=reply, reason=detail,
        ),
        SubGoodsReceiptStateField.TASK_RESULT: TaskResult(
            task_result=reply, task_result_explain=detail, task_result_code=1
        ),
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=node,
            execute_result_code=1,
            execute_result_msg=msg,
            execute_exceptions=[detail]
        )]
    }
