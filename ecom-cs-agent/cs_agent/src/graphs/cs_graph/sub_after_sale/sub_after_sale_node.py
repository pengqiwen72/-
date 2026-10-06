"""
售后处理子图的节点。

链路：取订单事实 → 政策引擎定方案 →（高金额）人工审批 → 执行 + 幂等记账。

HITL × 幂等的四条不变量
-----------------------
这四条是设计出来的，不是碰巧成立的，任何一条破了都会出现「同一次诉求退两次钱」：

  ① **`interrupt()` 所在节点在同一次 resume 中会从首行重放**，所以中断之前
     **不得有任何副作用**。`approval_node` 全程只读状态，退款/建单全部放在
     它**之后**的 `execute_node` 里 —— 这是结构上的保证，不依赖记账是否正确。
  ② **plan_id 由 `order_id + action_type` 确定性生成**（uuid5），重放、重试、
     多轮审批都指向同一个 ID，不会每轮生成一个新的。
  ③ **执行节点自己做幂等**：重复 resume 时 `IdempotencyGuard` 短路返回首次结果。
  ④ **审批记录以 `thread_id:plan_id` 为键，首次写入生效**，让审批节点本身
     也可重放 —— 否则「批准」这件事会被记两遍。

关于「要不要让模型润色售后话术」
--------------------------------
**不让。** 售后结论是承诺性内容（金额、时效、责任划分），每一个字都可能被客户
当作凭据。政策模板已经把话说全了，模型能带来的只有措辞上的收益，风险却是改写
数字或超出政策口径。所以这条链路**完全不经过大模型** —— 这不是省事，
而是「模型只在收益大于风险的地方出现」这个判断的具体体现。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from graphs.cs_graph.cs_node import IntentDetection
from graphs.cs_graph.sub_after_sale.sub_after_sale_state import (
    SubAfterSaleState,
    SubAfterSaleStateField,
)
from integrations import policy
from integrations.factory import get_handoff_port, get_logistics_port, get_order_port, get_refund_port
from integrations.idempotency import IdempotencyConflict, IdempotencyGuard, build_key
from integrations.normalize import normalize_logistics, normalize_order
from integrations.policy import PolicyFacts, days_since
from integrations.ports import IntegrationError
from models.cs_models import AfterSalePlan, NodeResult, TaskResult
from observability.progress import emit, node_done
from utils.agent_logger import get_logger
from utils.text import last_question_clause

logger = get_logger("sub_after_sale_node")

CUSTOMER_SENDER = "客户"

_ORDER_ID = re.compile(r"\b(SO\d{6,})\b", re.IGNORECASE)

#: 动作类型 -> 执行时调用哪个端口。
#: 只有真的会产生资金流动的动作才走退款端口；换货与补偿走工单，
#: 由人来发券/发货 —— 让「自动化的边界在哪」在代码里一眼可见。
_REFUND_ACTIONS = {"auto_refund", "return_refund"}
_TICKET_ACTIONS = {"exchange", "compensate", "handoff"}


class NodeName(str, Enum):
    WELCOME  = "after_sale_welcome_node"
    MATCH    = "after_sale_match_node"
    APPROVAL = "after_sale_approval_node"
    EXECUTE  = "after_sale_execute_node"


# ---------------------------------------------------------------- 取事实

def _customer_text(state: SubAfterSaleState) -> str:
    raw = (state.get(SubAfterSaleStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.AFTER_SALE.value:
        return raw

    history = state.get(SubAfterSaleStateField.CHAT_HISTORY)
    for msg in reversed(getattr(history, "chat_msgs", None) or []):
        if getattr(msg, "sender", "") != CUSTOMER_SENDER:
            continue
        content = (getattr(msg, "content", "") or "").strip()
        if content:
            return last_question_clause(content)
    return ""


def _resolve_order_id(state: SubAfterSaleState) -> str:
    """售后必须落到一个具体订单上，找不到就不猜。"""
    hit = _ORDER_ID.search(_customer_text(state) or "")
    if hit:
        return hit.group(1).upper()

    history = state.get(SubAfterSaleStateField.ORDER_HISTORY)
    orders = getattr(history, "orders", None) or []
    for order in orders:
        order_id = getattr(order, "order_id", "")
        if order_id:
            return order_id
    return ""


def _signed_at(order_id: str, created_at: str) -> Tuple[str, Optional[str]]:
    """签收时间。

    优先从物流轨迹里取「已签收」那个节点 —— 政策写的是「签收后 N 天」，
    签收时间只有一个权威来源。物流查不到时退回下单时间，并在降级里说明：
    用下单时间算出来的天数**偏大**，会让本可以自动处理的申请落到人工。
    """
    try:
        raw = get_logistics_port().get_trace(order_id)
    except IntegrationError as e:
        return created_at, f"物流信息不可用（{e}），签收时间按下单时间估算"
    except Exception as e:  # pragma: no cover
        return created_at, f"物流查询异常（{e}），签收时间按下单时间估算"

    normalized = normalize_logistics(raw, order_id=order_id)
    for node in reversed(normalized["nodes"]):
        if "已签收" in (node.get("status") or "") or "已签收" in (node.get("desc") or ""):
            return node["time"], None
    return created_at, "物流轨迹里没有签收记录，签收时间按下单时间估算"


# ---------------------------------------------------------------- 节点：定方案

def welcome_node(state: SubAfterSaleState, config: RunnableConfig):
    """进政策匹配之前先报一声。"""
    logger.info("=== 售后处理 ===")
    emit("policy_match", detail="准备匹配售后政策规则库")
    return {
        SubAfterSaleStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME.value,
            execute_result_code=0,
            execute_result_msg="正在核对该订单的售后政策，请稍等。",
            execute_exceptions=[]
        )]
    }


def match_node(state: SubAfterSaleState, config: RunnableConfig):
    """取订单事实 -> 匹配政策 -> 产出方案。**不产生任何副作用。**"""
    logger.info("=== 售后政策匹配 ===")

    order_id = _resolve_order_id(state)
    reason = _customer_text(state)

    if not order_id:
        logger.warning("没有定位到订单，无法匹配售后政策")
        return _failure(
            NodeName.MATCH.value, "没有定位到订单",
            "麻烦提供一下订单号，我这边帮您核对售后政策。",
            "task_input 与订单历史都没有订单号",
        )

    try:
        raw_order = get_order_port().get_order(order_id)
    except IntegrationError as e:
        return _failure(NodeName.MATCH.value, "订单系统不可用",
                        "订单系统暂时查不了，我先帮您转接人工客服。", str(e))

    if raw_order is None:
        return _failure(NodeName.MATCH.value, "订单不存在",
                        f"没有查到订单 {order_id}，请核对一下订单号。",
                        f"订单中台没有 {order_id}")

    order = normalize_order(raw_order)
    if not order["status_code"]:
        return _failure(NodeName.MATCH.value, "订单状态无法识别",
                        f"订单 {order_id} 的状态读取异常，已转人工核实。",
                        f"上游状态值无法映射：{raw_order.get('tradeStatus')}")

    signed_at, degrade_note = _signed_at(order_id, order["created_at"])
    days = days_since(signed_at)

    sku_ids = [item["sku_id"] for item in order["items"] if item["sku_id"]]

    facts = PolicyFacts(
        order_id=order_id,
        reason=reason,
        amount=order["pay_amount"],
        days_since_signed=days,
        status_code=order["status_code"],
        is_customized=policy.is_customized(sku_ids),
        sku_ids=sku_ids,
    )

    matched = policy.match(facts)
    if matched is None:
        return _failure(NodeName.MATCH.value, "规则库为空",
                        "售后规则暂时不可用，已为您转接人工客服。",
                        "政策规则库没有加载出任何规则")

    plan = _build_plan(matched, facts)

    # 判定依据逐条外显：这是「为什么是这个方案、为什么不是那个」的唯一证据
    for line in matched.trace:
        emit("policy_match", detail=f"✓ {line}")
    for line in matched.rejected:
        emit("policy_match", detail=f"✗ {line}")
    if degrade_note:
        emit("handoff", detail=degrade_note)

    node_done(NodeName.MATCH.value, f"{matched.policy_id} -> {plan.action_type}")

    return {
        SubAfterSaleStateField.AFTER_SALE: plan,
        SubAfterSaleStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.MATCH.value,
            execute_result_code=0,
            execute_result_msg=f"命中政策 {matched.policy_id}（{plan.refund_type}）",
            execute_exceptions=[degrade_note] if degrade_note else []
        )]
    }


def _build_plan(matched, facts: PolicyFacts) -> AfterSalePlan:
    from integrations.idempotency import build_plan_id

    action = matched.action
    reason_text = matched.render_reason()

    # 模板里如果带了 alternative（比如「可提供 30 元优惠券」），
    # 这里把尾部空格收拾干净，免得答复里出现两个句号
    reason_text = reason_text.replace("。 ", "。").strip()

    message = {
        "auto_refund": f"已为您发起全额退款 {facts.amount:.2f} 元。",
        "return_refund": "已为您登记退货退款，请按页面提示寄回商品。",
        "exchange": "已为您登记换货，售后专员会联系您确认。",
        "compensate": "已为您申请物流超时补偿。",
        "reject": "很抱歉，这笔申请按店铺政策无法受理。",
        "handoff": "已为您转接人工客服，稍后会有专员跟进。",
    }.get(matched.action_type, "已记录您的申请。")

    if matched.need_approval:
        message = f"该笔申请金额 {facts.amount:.2f} 元，需人工审批后执行，正在等待审批。"

    return AfterSalePlan(
        plan_id=build_plan_id(facts.order_id, matched.action_type),
        order_id=facts.order_id,
        action_type=matched.action_type,
        refund_type=action.get("refund_type", ""),
        policy_id=matched.policy_id,
        policy_title=matched.title,
        priority=matched.priority,
        amount=facts.amount,
        need_approval=matched.need_approval,
        sla=action.get("sla", ""),
        reason=reason_text,
        # ✓ / ✗ 直接写进字符串本身，而不是留给每个消费方各自加前缀。
        # 方案会同时出现在终端、SSE 事件和后台页面里，标记只在这一处生成，
        # 才不会出现「终端显示 ✗、后台显示 ✓」这种同一份数据两种解读的情况。
        reason_trace=[f"✓ {t}" for t in matched.trace]
        + [f"✗ {r}" for r in matched.rejected],
        status="pending_approval" if matched.need_approval else "matched",
        message=message,
    )


# ---------------------------------------------------------------- 节点：审批

def approval_node(state: SubAfterSaleState, config: RunnableConfig):
    """人工审批。

    ⚠️ 这个节点在恢复时会**从第一行整体重放**，所以中断之前只能读状态、
    不能有任何副作用。所有写操作都在 `execute_node` 里。
    """
    plan = state.get(SubAfterSaleStateField.AFTER_SALE)
    if plan is None:  # pragma: no cover - 路由保证不会走到
        return _failure(NodeName.APPROVAL.value, "没有待审批的方案",
                        "审批失败，请重试。", "状态里没有 after_sale")

    logger.info(f"=== 等待人工审批：{plan.plan_id} ===")
    emit("hitl_wait", detail=f"{plan.policy_title}：{plan.amount:.2f} 元待审批")

    decision = interrupt({
        "type": "after_sale_approval",
        "plan_id": plan.plan_id,
        "order_id": plan.order_id,
        "amount": plan.amount,
        "refund_type": plan.refund_type,
        "reason": plan.reason,
        "reason_trace": plan.reason_trace,
        "description": (
            f"{plan.policy_title}：订单 {plan.order_id} 需退款 {plan.amount:.2f} 元，"
            f"超过自动处理额度，请确认是否执行"
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
            f"审批通过，正在执行退款 {plan.amount:.2f} 元。" if approved
            else f"审批未通过，订单 {plan.order_id} 的这笔申请已驳回，将转人工说明。"
        ),
    })

    emit("policy_match", detail=f"审批{'通过' if approved else '驳回'}：{plan.plan_id}")

    return {
        SubAfterSaleStateField.AFTER_SALE: updated,
        SubAfterSaleStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.APPROVAL.value,
            execute_result_code=0,
            execute_result_msg=f"人工审批{'通过' if approved else '驳回'}（{plan.plan_id}）",
            execute_exceptions=[]
        )]
    }


def _parse_decision(decision: Any) -> Tuple[bool, str]:
    """审批入参归一：把各调用方的写法收敛成一个布尔 + 一个署名。

    三种形态都要认：
      * `{"confirmed": "ok"}` —— 图内 HITL 协议（demo runner 与 SSE 恢复用这个）
      * `{"approved": True, "operator": "CS001"}` —— 后台审批接口的结构化写法
      * `"ok"` —— 交互式终端直接给的字符串

    ⚠️ **必须先认 `confirmed`**：`interrupt()` 会把 resume 的值原样返回，
    而 demo runner 包的是 `{"confirmed": "ok"}`。只认 `approved` 的话，
    一次「通过」会被判成「驳回」——而且日志、事件、结果码全都是正常的，
    只有审批结论是反的，是最难从现象倒推回来的那类 bug。

    「重新生成」在这里与「驳回」同义：售后方案由政策规则唯一确定，
    重跑一遍只会得到同一份方案，有意义的是让人工介入而不是再算一次。
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


#: 图内 HITL 协议的字段名，与 sub_tag / sub_profile 的 `HumanFeedback.FIELD_NAME` 是同一个。
#: 恢复值会被 `interrupt()` 原样返回，所以解析必须能认出这一层包装。
_CONFIRMED = "confirmed"

#: 视为「通过」的取值（覆盖终端、后台接口、SSE 三种调用方的写法）
_APPROVE_VALUES = {"ok", "approve", "approved", "yes", "pass", "通过"}


def _thread_id(config: RunnableConfig) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id", "") or "")


# ---------------------------------------------------------------- 节点：执行

def execute_node(state: SubAfterSaleState, config: RunnableConfig):
    """执行方案。**这是唯一有副作用的节点**，也是幂等保护落点。"""
    plan = state.get(SubAfterSaleStateField.AFTER_SALE)
    if plan is None:  # pragma: no cover
        return _failure(NodeName.EXECUTE.value, "没有可执行的方案",
                        "执行失败，请重试。", "状态里没有 after_sale")

    if plan.status == "rejected":
        logger.info(f"方案 {plan.plan_id} 已被驳回，不执行")
        plan = plan.model_copy(update={"status": "rejected"})
        return _done(plan, "审批驳回，未执行任何写操作")

    if plan.action_type in ("reject",) or plan.action_type not in (_REFUND_ACTIONS | _TICKET_ACTIONS):
        # 拒绝类方案不产生写操作；未知动作同样不执行 —— 宁可少做也不能乱做
        logger.info(f"动作 {plan.action_type} 无需写操作")
        return _done(plan.model_copy(update={"status": "matched"}), "按政策不予受理，无需执行")

    guard = IdempotencyGuard()
    payload = {
        "order_id": plan.order_id,
        "action_type": plan.action_type,
        "amount": plan.amount,
        "refund_type": plan.refund_type,
        "policy_id": plan.policy_id,
    }
    key = build_key(plan.order_id, plan.action_type)

    try:
        record = guard.begin(order_id=plan.order_id, action_type=plan.action_type, payload=payload)
    except IdempotencyConflict as conflict:
        # 同一个订单 + 同一个动作，但请求内容不同 —— 不猜、不静默复用，交给人
        logger.error(f"幂等冲突：{conflict}")
        emit("idempotent_replay", detail=f"幂等冲突：{conflict.key} 的请求内容与首次不一致，已转人工")
        updated = plan.model_copy(update={
            "status": "conflict",
            "message": "这笔订单的同类申请此前已提交过、且内容不一致，为避免重复处理，已转人工核实。",
        })
        updated.refund_id = ""
        return _done(updated, "幂等冲突，转人工核实")

    if record.status == "done":
        # 短路：不再调下游，直接把首次的结果返回去
        logger.info(f"幂等命中，短路执行：{key}")
        emit("idempotent_replay", detail=f"命中幂等记录 {key}，未重复发起")
        replayed = plan.model_copy(update={
            "status": "replayed",
            "replayed": True,
            "refund_id": str((record.result or {}).get("refund_id", "") or ""),
            "message": (
                f"这笔申请之前已经处理过了（单号 {record.result.get('refund_id', '—')}），"
                f"本次没有重复发起。"
            ),
        })
        return _done(replayed, "命中幂等，未重复执行")

    # ---- 真正执行 ----
    try:
        result = _submit(plan)
    except IntegrationError as e:
        guard.fail(key, str(e))
        logger.error(f"售后执行失败：{e}")
        return _failure(NodeName.EXECUTE.value, "执行失败",
                        "提交售后申请时接口异常，已转人工客服跟进。", str(e))

    guard.complete(key, result)
    emit("refund_submitted", detail=(
        f"{plan.refund_type}已提交，单号 {result.get('refund_id') or result.get('ticket_id') or '—'}"
    ))

    updated = plan.model_copy(update={
        "status": "executed",
        "refund_id": str(result.get("refund_id") or result.get("ticket_id") or ""),
        "message": _executed_message(plan, result),
    })
    return _done(updated, f"已执行（{plan.refund_type}）")


def _submit(plan: AfterSalePlan) -> Dict[str, Any]:
    """按动作类型选择下游端口。"""
    common = {
        "order_id": plan.order_id,
        "plan_id": plan.plan_id,
        "amount": plan.amount,
        "refund_type": plan.refund_type,
        "reason": plan.reason,
        "operator": plan.approved_by,
        "idempotency_key": build_key(plan.order_id, plan.action_type),
    }

    if plan.action_type in _REFUND_ACTIONS:
        return get_refund_port().submit_refund(common)

    return get_handoff_port().create_ticket({
        **common,
        "title": f"{plan.policy_title}：订单 {plan.order_id}",
        "detail": f"{plan.reason}\n处理方式：{plan.refund_type}\n时效：{plan.sla}",
        "priority": "high" if plan.amount >= 500 else "normal",
    })


def _executed_message(plan: AfterSalePlan, result: Dict[str, Any]) -> str:
    number = result.get("refund_id") or result.get("ticket_id") or ""
    if plan.action_type in _REFUND_ACTIONS:
        head = f"已为您提交{plan.refund_type}"
    else:
        head = f"已为您创建{plan.refund_type}工单"
    return f"{head}{'（单号 ' + number + '）' if number else ''}，{plan.sla or '我们会尽快跟进'}。"


# ---------------------------------------------------------------- 返回封装

def _done(plan: AfterSalePlan, note: str) -> Dict[str, Any]:
    return {
        SubAfterSaleStateField.AFTER_SALE: plan,
        SubAfterSaleStateField.TASK_RESULT: TaskResult(
            task_result=plan.render(),
            task_result_explain=f"{plan.policy_id}：{note}",
            # 转人工、拒绝、命中幂等、审批驳回都是**正常业务结论**，不是失败。
            # 判成失败会让上层把正确的兜底当 bug。
            task_result_code=0
        ),
        SubAfterSaleStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.EXECUTE.value if plan.status in
            ("executed", "replayed", "conflict", "matched", "rejected") else NodeName.MATCH.value,
            execute_result_code=0,
            execute_result_msg=note,
            execute_exceptions=[]
        )]
    }


def _failure(node: str, msg: str, reply: str, detail: str) -> Dict[str, Any]:
    logger.warning(f"{msg}：{detail}")
    return {
        SubAfterSaleStateField.AFTER_SALE: AfterSalePlan(
            action_type="handoff", status="matched",
            policy_title=msg, message=reply, reason=detail,
        ),
        SubAfterSaleStateField.TASK_RESULT: TaskResult(
            task_result=reply, task_result_explain=detail, task_result_code=1
        ),
        SubAfterSaleStateField.NODE_RESULT: [NodeResult(
            execute_node_name=node,
            execute_result_code=1,
            execute_result_msg=msg,
            execute_exceptions=[detail]
        )]
    }
