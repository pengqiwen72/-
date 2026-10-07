"""
售后处理子图。

    welcome → match ─┬─(高金额)─→ approval ─→ execute → END
                     └─(其余)──────────────→ execute

审批只是**多一跳**，执行永远只有一处 —— 这样「有副作用的代码只有一份」
是图结构保证的，而不是靠两个分支各自记得写对。
"""

from langgraph.graph import START, END, StateGraph

from graphs.cs_graph.sub_after_sale.sub_after_sale_node import (
    NodeName,
    approval_node,
    execute_node,
    match_node,
    welcome_node,
)
from graphs.cs_graph.sub_after_sale.sub_after_sale_state import (
    SubAfterSaleInputState,
    SubAfterSaleOutputState,
    SubAfterSaleState,
    SubAfterSaleStateField,
)


def _route_after_match(state: SubAfterSaleState) -> str:
    """高金额方案先审批，其余直接执行。

    判据用 `status == "pending_approval"` 而不是 `need_approval`：前者是
    `match` 节点明确写下的「这单在等审批」，后者只是政策上的一个属性。
    取资料失败等降级分支产出的也是 `need_approval=False` 的方案，
    用属性判会把它们按「不需要审批」直接放行到执行节点。
    """
    plan = state.get(SubAfterSaleStateField.AFTER_SALE)
    if plan is not None and plan.status == "pending_approval" and plan.need_approval:
        return NodeName.APPROVAL.value
    return NodeName.EXECUTE.value


builder = StateGraph(
    state_schema=SubAfterSaleState,
    input_schema=SubAfterSaleInputState,
    output_schema=SubAfterSaleOutputState,
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
    },
)
builder.add_edge(NodeName.APPROVAL.value, NodeName.EXECUTE.value)
builder.add_edge(NodeName.EXECUTE.value, END)

sub_after_sale_graph = builder.compile()
