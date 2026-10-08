"""
采购申请子图的节点。

链路：欢迎 → 要素提取 + 定方案 →（大额）人工审批 → 执行 + 幂等记账。

HITL × 幂等的四条不变量（与售后链路同源，这条链路逐条复用）
------------------------------------------------------------
  ① **`interrupt()` 所在节点在 resume 时会从第一行重放**，所以中断之前不得有
     任何副作用。`approval_node` 全程只读状态，下单只有一个落点 `execute_node`。
  ② **plan_id 由 biz_id（确定性 PR 单号）uuid5 生成**，重放、重试、多轮审批指向
     同一个 ID，不会每轮换一个。
  ③ **执行节点自己做幂等**：重复 resume 时 `IdempotencyGuard` 短路返回首次结果。
  ④ **审批记录以 `thread_id:plan_id` 为键，首次写入生效** —— 让审批节点本身也可
     重放，否则「批准」会被记两遍。

单号为什么是确定性的
--------------------
`build_request_id(物料, 数量, 到货仓, 提报人)` 四个要素拼出 `PR{日期}{6位哈希}`：
同一笔申请重发多少次都是同一个单号，plan_id、幂等键、审批记录全部落在这个锚点上。
要素改了（数量、仓库变了）就是另一笔申请，理应得到新单号 —— 这是**业务语义上**的
「另一笔」，不是重放。

Phase 2 的简化之处（都标了 TODO，Phase 5 换掉时调用点不动）
------------------------------------------------------------
* 审批阈值写死 50000 元，Phase 5 换成 `integrations/policy.py` 读
  `data/seed/purchase_policy.json`（大额 / 新供应商 / 交期过短 / 高于均价 等条件）。
* 单价取物料的参考价，Phase 5 改为按供应商报价单取价并落比价依据。
* `_submit` 还没有下游端口（Phase 4 建 `get_purchase_port()`），先返回确定性回执，
  把幂等这一环先跑通 —— 换端口时只改这一个函数体。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.sub_purchase_request.sub_purchase_request_state import (
    SubPurchaseRequestState,
    SubPurchaseRequestStateField,
)
from integrations.idempotency import (
    IdempotencyConflict,
    IdempotencyGuard,
    build_key,
    build_plan_id,
    build_request_id,
)
from llm.llm_purchase_draft import llm_purchase_draft
from models.scm_models import MaterialInfo, NodeResult, PurchasePlan, TaskResult
from observability.progress import emit, node_done
from utils.agent_logger import get_logger

logger = get_logger("sub_purchase_request_node")

#: 需要人工审批的申请金额门槛（元）。
#: TODO(Phase 5)：改为策略引擎 `load_policies("purchase_policy")` 的判定，
#: 这条常量退化为「兜底策略」的参数。
_APPROVAL_AMOUNT = 50000.0

#: 供应商没给交期时按几天排预计到货 —— 兜底值而不是猜真实的，会写进方案里给人看
_DEFAULT_LEAD_TIME_DAYS = 7


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
    """抽要素 → 定方案。**不产生任何副作用。**"""
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

    # 单价口径：Phase 2 取物料参考价；Phase 5 起按供应商报价单取价（有报价用报价，
    # 没有才回参考价），并记录「价差多少、为什么选了这家」作为审批依据。
    unit_price = material.unit_price_ref
    amount = round(draft.qty * unit_price, 2)

    supplier = next((s for s in suppliers if s.supplier_code == draft.supplier_code), None)

    lead_time = supplier.lead_time_days if supplier and supplier.lead_time_days else _DEFAULT_LEAD_TIME_DAYS
    expect_arrival = (datetime.now() + timedelta(days=lead_time)).strftime("%Y-%m-%d")

    # ---- 审批判定（Phase 2：单一金额阈值；Phase 5：策略引擎全条件）----
    need_approval = amount >= _APPROVAL_AMOUNT
    if need_approval:
        trace = [f"✓ 申请金额 {amount:.2f} 元 ≥ {_APPROVAL_AMOUNT:.0f} 元，需人工审批"]
        title = "大额采购审批"
    else:
        trace = [f"✗ 申请金额 {amount:.2f} 元 < {_APPROVAL_AMOUNT:.0f} 元，免审批自动执行"]
        title = "常规采购申请"

    operator_id = _operator_id(state)
    biz_id = build_request_id(
        material.material_code, draft.qty, draft.target_warehouse, operator_id
    )

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
        supplier_code=draft.supplier_code,
        supplier_name=supplier.supplier_name if supplier else "",
        expect_arrival=expect_arrival,
        need_approval=need_approval,
        policy_id="PUR-THRESHOLD",
        policy_title=title,
        priority=100,
        reason=(
            f"申请金额 {amount:.2f} 元"
            + ("，超过自动处理额度" if need_approval else "，在自动处理额度内")
        ),
        reason_trace=trace,
        status="pending_approval" if need_approval else "matched",
        operator_id=operator_id,
        description=" ".join(text.split()),
        message=(
            f"该笔申请金额 {amount:.2f} 元，需人工审批后执行，正在等待审批。"
            if need_approval
            else f"申请已受理，正在为您下达采购订单（金额 {amount:.2f} 元）。"
        ),
    )

    # 判定依据逐条外显：这是「为什么这单要审、那单不用」的唯一证据
    for line in trace:
        emit("policy_match", detail=line)

    node_done(NodeName.MATCH.value, f"{biz_id} amount={amount:.2f} approval={need_approval}")

    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: plan,
        SubPurchaseRequestStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.MATCH.value,
            execute_result_code=0,
            execute_result_msg=f"生成采购方案 {biz_id}（{title}）",
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

    logger.info(f"=== 等待人工审批：{plan.plan_id} ===")
    emit("hitl_wait", detail=f"{plan.material_name}：{plan.amount:.2f} 元待审批")

    decision = interrupt({
        "type": "purchase_approval",
        "plan_id": plan.plan_id,
        "biz_id": plan.biz_id,
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

    approved, operator = _parse_decision(decision)
    logger.info(f"审批结论：{'通过' if approved else '驳回'}（{operator or '未署名'}）")

    # 审批记录首次写入生效：这个节点会被重放，重放不应该记第二笔
    thread_id = _thread_id(config)
    guard = IdempotencyGuard()
    is_first = guard.record_approval(
        thread_id=thread_id, plan_id=plan.plan_id,
        decision="approved" if approved else "rejected", operator=operator,
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


def _parse_decision(decision: Any) -> Tuple[bool, str]:
    """审批入参归一：把各调用方的写法收敛成一个布尔 + 一个署名。

    三种形态都要认（与售后链路同一份协议，不另立一套）：
      * `{"confirmed": "ok"}` —— 图内 HITL 协议（demo runner 与 SSE 恢复用这个）
      * `{"approved": True, "operator": "OP002"}` —— 后台审批接口的结构化写法
      * `"ok"` —— 交互式终端直接给的字符串

    ⚠️ **必须先认 `confirmed`**：`interrupt()` 会把 resume 的值原样返回，
    而 demo runner 包的是 `{"confirmed": "ok"}`。只认 `approved` 的话，
    一次「通过」会被判成「驳回」——而且日志、事件、结果码全都是正常的，
    只有审批结论是反的，是最难从现象倒推回来的那类 bug。

    Phase 5 在这里加第三段语义 `recreate`（重走策略引擎，可带 patch 改被驳回
    的字段）—— 那是「一次通过率」指标唯一可演示的机制，解析层先把位置留出来。
    """
    if isinstance(decision, dict):
        operator = str(decision.get("operator", "") or "")
        if _CONFIRMED in decision:
            return str(decision.get(_CONFIRMED, "")).strip().lower() in _APPROVE_VALUES, operator
        approved = decision.get("approved")
        if approved is None:
            approved = str(decision.get("decision", "")).lower() in _APPROVE_VALUES
        return bool(approved), operator

    return str(decision or "").strip().lower() in _APPROVE_VALUES, ""


#: 图内 HITL 协议的字段名，与其他 HITL 子图共用（恢复值会被原样返回）
_CONFIRMED = "confirmed"

#: 视为「通过」的取值（覆盖终端、后台接口、SSE 三种调用方的写法）
_APPROVE_VALUES = {"ok", "approve", "approved", "yes", "pass", "通过"}


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
        emit("idempotent_replay", detail=f"幂等冲突：{conflict.key} 的要素与首次不一致，已转人工")
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
    """提交采购订单到下游系统。

    TODO(Phase 4/5)：换成 `get_purchase_port().create_purchase_order(...)`。
    现在返回的是**确定性回执**（单号由申请单号派生，同日同要素必同号）——
    这一步的意义在于把「幂等账本里记了什么、重放时短路返回什么」先跑通，
    端口接上之后 `execute_node` 一行都不用改。

    刻意**不**直接写 store：种子库是只读事实源（装载节点逐次读它，
    测试按计数断言），往里面塞运行期数据会让「同一份种子连查两次结果不同」。
    真实的下单落库是端口的职责，Phase 4 由 mock 端口写运行期命名空间。
    """
    po_no = "PO" + plan.biz_id[2:] if plan.biz_id.startswith("PR") else plan.biz_id
    return {"po_no": po_no, "status": "created", "amount": plan.amount}


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
    logger.warning(f"{msg}：{detail}")
    return {
        SubPurchaseRequestStateField.PURCHASE_REQUEST: PurchasePlan(
            status="matched", policy_title=msg, message=reply, reason=detail,
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
