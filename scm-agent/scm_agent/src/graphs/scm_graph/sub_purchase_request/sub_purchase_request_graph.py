"""
采购申请子图。

    welcome → match ─┬─(待审批)─→ approval ─┬─(改判重报且仍需审)─→ approval（循环）
                     │                     ├─(失败)────────────→ END
                     │                     └─(其余)────────────→ execute → END
                     ├─(失败)──────────────────────────────────→ END
                     └─(其余)───────────────────────────────────→ execute → END

审批只是**多一跳**，执行永远只有一处 —— 「有副作用的代码只有一份」是图结构
保证的，而不是靠两个分支各自记得写对。recreate 改判重报走的是 approval 的
自回路：复判仍需审批就在原地再来一轮（新一轮审批卡带新方案），降为免审批
就直接去执行 —— 「重走策略引擎」这个语义由路由函数兑现，节点本身不动图。
"""

from langgraph.graph import START, END, StateGraph

from graphs.scm_graph.scm_state import ScmConfig
from graphs.scm_graph.sub_purchase_request.sub_purchase_request_node import (
    NodeName,
    approval_node,
    execute_node,
    match_node,
    welcome_node,
)
from graphs.scm_graph.sub_purchase_request.sub_purchase_request_state import (
    SubPurchaseRequestInputState,
    SubPurchaseRequestOutputState,
    SubPurchaseRequestState,
    SubPurchaseRequestStateField,
)


def _route_after_match(state: SubPurchaseRequestState) -> str:
    """待审批先审批，失败直接结束，其余执行。

    判据用 `status == "pending_approval"` 而不是 `need_approval`：前者是
    `match` 节点明确写下的「这单在等审批」，后者只是策略上的一个属性。

    `failed`（要素缺失、供应商不存在、策略库不可用）必须路由到 END：
    失败方案没有任何可执行的要素，流过执行节点会拿空要素去下单 ——
    这条曾经真的发生过（失败方案的 status 曾是 "matched"，在条件边眼里
    和「免审批方案」一模一样）。
    """
    plan = state.get(SubPurchaseRequestStateField.PURCHASE_REQUEST)
    if plan is not None and plan.status == "failed":
        return END
    if plan is not None and plan.status == "pending_approval" and plan.need_approval:
        return NodeName.APPROVAL.value
    return NodeName.EXECUTE.value


def _route_after_approval(state: SubPurchaseRequestState) -> str:
    """审批后：复判仍待审回到审批（recreate 第二轮的落点），失败结束，其余执行。

    recreate 后重新命中了需审条款时，节点写下的状态还是 `pending_approval`
    （新一轮的「在等审批」），于是回到 approval 再中断一轮 ——
    「同一笔申请多轮审批」在图上是同一个节点的自回路，幂等靠 plan_id +
    revision 感知的审批记录键兜住（见 `_recreate_node`）。
    """
    plan = state.get(SubPurchaseRequestStateField.PURCHASE_REQUEST)
    if plan is not None and plan.status == "failed":
        return END
    if plan is not None and plan.status == "pending_approval" and plan.need_approval:
        return NodeName.APPROVAL.value
    return NodeName.EXECUTE.value


builder = StateGraph(
    state_schema=SubPurchaseRequestState,
    input_schema=SubPurchaseRequestInputState,
    output_schema=SubPurchaseRequestOutputState,
    config_schema=ScmConfig,
)

builder.add_node(NodeName.WELCOME.value, welcome_node)
builder.add_node(NodeName.MATCH.value, match_node)
builder.add_node(NodeName.APPROVAL.value, approval_node)
builder.add_node(NodeName.EXECUTE.value, execute_node)

builder.add_edge(START, NodeName.WELCOME.value)
builder.add_edge(NodeName.WELCOME.value, NodeName.MATCH.value)
builder.add_conditional_edges(
    NodeName.MATCH.value,
    _route_after_match,
    {
        NodeName.APPROVAL.value: NodeName.APPROVAL.value,
        NodeName.EXECUTE.value: NodeName.EXECUTE.value,
        END: END,
    },
)
builder.add_conditional_edges(
    NodeName.APPROVAL.value,
    _route_after_approval,
    {
        NodeName.APPROVAL.value: NodeName.APPROVAL.value,
        NodeName.EXECUTE.value: NodeName.EXECUTE.value,
        END: END,
    },
)
builder.add_edge(NodeName.EXECUTE.value, END)

sub_purchase_request_graph = builder.compile()
