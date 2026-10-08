"""
收货验收子图的节点。

链路：欢迎 → 要素提取 + 质检判定 →（不良率超阈值）人工复核 → 执行 + 幂等记账。

与采购申请同一条 HITL × 幂等骨架（四条不变量见 `sub_purchase_request_node`），
差别只在触发条件与复核对象：

  * 采购看**金额**（要不要花这笔钱），收货看**不良率**（这批货能不能收）。
  * 复核的产物是处置方式：入库 / 退货（Phase 5 起还有让步接收、换货与
    recreate+patch —— 比如复核人把「退货」改成「让步接收」）。

不良率口径只有一处
------------------
`ReceiptPlan.compute_defect_rate` 是唯一计算处：阈值判定、方案里的数字、
给人看的文案读的都是同一个值。若各写一份 `defect / received`，
演示时「判定说超标、页面显示没超」这种自相矛盾迟早会出现。

Phase 2 的简化之处（都标了 TODO，Phase 5 换掉时调用点不动）
------------------------------------------------------------
* 复核阈值写死 2%，Phase 5 换成 `data/seed/quality_policy.json` 的策略引擎
  （QC-001 不良率 > 2% 需复核 等）。
* `_submit` 还没有下游端口（Phase 4 建 `get_receipt_port()`），先返回确定性回执。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.sub_goods_receipt.sub_goods_receipt_state import (
    SubGoodsReceiptState,
    SubGoodsReceiptStateField,
)
from integrations.idempotency import (
    IdempotencyConflict,
    IdempotencyGuard,
    build_key,
    build_plan_id,
    build_receipt_id,
)
from llm.llm_receipt_draft import llm_receipt_draft
from models.scm_models import NodeResult, PurchaseOrderInfo, ReceiptPlan, TaskResult
from observability.progress import emit, node_done
from utils.agent_logger import get_logger

logger = get_logger("sub_goods_receipt_node")

#: 需要人工复核的不良率阈值。
#: TODO(Phase 5)：改为 `load_policies("quality_policy")` 的判定（QC-001），
#: 这条常量退化为兜底策略的参数。
_REVIEW_DEFECT_RATE = 0.02


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

    # ---- 复核判定（Phase 2：单一不良率阈值；Phase 5：策略引擎全条件）----
    need_review = defect_rate > _REVIEW_DEFECT_RATE
    if need_review:
        trace = [f"✓ 不良率 {defect_rate:.2%} > {_REVIEW_DEFECT_RATE:.0%}，需质检复核"]
        title = "到货质检复核"
        disposition = "待复核"
    else:
        trace = [f"✗ 不良率 {defect_rate:.2%} ≤ {_REVIEW_DEFECT_RATE:.0%}，正常入库"]
        title = "到货正常验收"
        disposition = "入库"

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
        policy_id="QC-THRESHOLD",
        policy_title=title,
        priority=100,
        reason=(
            f"实收 {draft.qty_received}，不良 {qty_defect}，不良率 {defect_rate:.2%}"
            + ("，超过复核阈值" if need_review else "，在允许范围内")
        ),
        reason_trace=trace,
        status="pending_review" if need_review else "matched",
        inspector=operator_id,
        message=(
            f"不良率 {defect_rate:.2%} 超过阈值，需人工复核后决定入库或退货，正在等待复核。"
            if need_review
            else f"到货验收合格（不良率 {defect_rate:.2%}），已办理入库。"
        ),
    )

    for line in trace:
        emit("quality_check", detail=line)

    node_done(NodeName.MATCH.value, f"{biz_id} defect_rate={defect_rate:.2%} review={need_review}")

    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: plan,
        SubGoodsReceiptStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.MATCH.value,
            execute_result_code=0,
            execute_result_msg=f"生成收货方案 {biz_id}（{title}）",
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

    logger.info(f"=== 等待质检复核：{plan.plan_id} ===")
    emit("hitl_wait", detail=f"{plan.material_name}：不良率 {plan.defect_rate:.2%} 待复核")

    decision = interrupt({
        "type": "quality_review",
        "plan_id": plan.plan_id,
        "biz_id": plan.biz_id,
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
        "reason": plan.reason,
        "reason_trace": plan.reason_trace,
        "description": (
            f"{plan.policy_title}：{plan.material_name}（{plan.material_code}）"
            f"实收 {plan.qty_received}，不良 {plan.qty_defect}（{plan.defect_rate:.2%}），"
            f"请确认处置方式"
        ),
    })

    approved, operator = _parse_decision(decision)
    logger.info(f"复核结论：{'通过' if approved else '驳回'}（{operator or '未署名'}）")

    thread_id = _thread_id(config)
    guard = IdempotencyGuard()
    is_first = guard.record_approval(
        thread_id=thread_id, plan_id=plan.plan_id,
        decision="approved" if approved else "rejected", operator=operator,
    )
    if not is_first:
        emit("idempotent_replay", detail=f"复核记录已存在，跳过重复写入（{plan.plan_id}）")

    updated = plan.model_copy(update={
        "status": "approved" if approved else "rejected",
        "approved_by": operator,
        # 复核通过的处置：不良品退回供应商、良品入库；复核驳回即整批拒收
        "disposition": "让步接收" if approved else "退货",
        "message": (
            f"复核通过：{plan.qty_received - plan.qty_defect} 件办理入库，"
            f"{plan.qty_defect} 件不良退回供应商。"
            if approved
            else f"复核未通过：整批拒收，{plan.qty_received} 件办理退货换货。"
        ),
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


def _parse_decision(decision: Any) -> Tuple[bool, str]:
    """复核入参归一 —— 与采购审批同一份协议、同一套三种形态（见那边的注释）。"""
    if isinstance(decision, dict):
        operator = str(decision.get("operator", "") or "")
        if _CONFIRMED in decision:
            return str(decision.get(_CONFIRMED, "")).strip().lower() in _APPROVE_VALUES, operator
        approved = decision.get("approved")
        if approved is None:
            approved = str(decision.get("decision", "")).lower() in _APPROVE_VALUES
        return bool(approved), operator

    return str(decision or "").strip().lower() in _APPROVE_VALUES, ""


_CONFIRMED = "confirmed"
_APPROVE_VALUES = {"ok", "approve", "approved", "yes", "pass", "通过"}


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
        emit("idempotent_replay", detail=f"幂等冲突：{conflict.key} 的登记内容与首次不一致，已转人工")
        updated = plan.model_copy(update={
            "status": "conflict",
            "message": "这笔到货此前已登记过、且数量不一致，为避免重复入库，已转人工核实。",
        })
        return _done(updated, "幂等冲突，转人工核实")

    if record.status == "done":
        logger.info(f"幂等命中，短路执行：{key}")
        emit("idempotent_replay", detail=f"命中幂等记录 {key}，未重复入库")
        replayed = plan.model_copy(update={
            "status": "replayed",
            "replayed": True,
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
        "message": _executed_message(plan, result),
    })
    return _done(updated, f"已执行（{plan.disposition}）")


def _submit(plan: ReceiptPlan) -> Dict[str, Any]:
    """把收货结果提交下游（入库单 / 退货单）。

    TODO(Phase 4/5)：换成 `get_receipt_port().submit_receipt(...)`。
    现在返回**确定性回执**（收货单号就是确定性的 biz_id），先把幂等这一环跑通；
    端口接上后 `execute_node` 一行都不用改。刻意不直接写 store，理由见
    `sub_purchase_request_node._submit`。
    """
    return {
        "receipt_no": plan.biz_id,
        "disposition": plan.disposition,
        "qty_stored": max(plan.qty_received - plan.qty_defect, 0),
        "qty_returned": plan.qty_defect,
    }


def _executed_message(plan: ReceiptPlan, result: Dict[str, Any]) -> str:
    if plan.disposition == "退货":
        return f"已办理退货换货（收货单 {result.get('receipt_no') or plan.biz_id}），请联系供应商安排退回。"
    return (
        f"已办理入库 {result.get('qty_stored', 0)} 件"
        f"{'，' + str(result.get('qty_returned')) + ' 件不良退回供应商' if result.get('qty_returned') else ''}"
        f"（收货单 {result.get('receipt_no') or plan.biz_id}）。"
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
    logger.warning(f"{msg}：{detail}")
    return {
        SubGoodsReceiptStateField.GOODS_RECEIPT: ReceiptPlan(
            status="matched", policy_title=msg, message=reply, reason=detail,
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
