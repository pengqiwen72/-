"""
收货验收子图。

    welcome → match ─┬─(不良率超阈值)─→ approval ─→ execute → END
                     └─(其余)───────────────────→ execute

与采购申请同型：复核只是多一跳，执行永远只有一处。
"""

from langgraph.graph import START, END, StateGraph

from graphs.scm_graph.scm_state import ScmConfig
from graphs.scm_graph.sub_goods_receipt.sub_goods_receipt_node import (
    NodeName,
    approval_node,
    execute_node,
    match_node,
    welcome_node,
)
from graphs.scm_graph.sub_goods_receipt.sub_goods_receipt_state import (
    SubGoodsReceiptInputState,
    SubGoodsReceiptOutputState,
    SubGoodsReceiptState,
    SubGoodsReceiptStateField,
)


def _route_after_match(state: SubGoodsReceiptState) -> str:
    """超阈值先复核，其余直接执行。

    判据用 `status == "pending_review"`（理由同采购子图：`need_review` 只是
    策略属性，降级分支产出的方案也是 False）。
    """
    plan = state.get(SubGoodsReceiptStateField.GOODS_RECEIPT)
    if plan is not None and plan.status == "pending_review" and plan.need_review:
        return NodeName.APPROVAL.value
    return NodeName.EXECUTE.value


builder = StateGraph(
    state_schema=SubGoodsReceiptState,
    input_schema=SubGoodsReceiptInputState,
    output_schema=SubGoodsReceiptOutputState,
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
    },
)
builder.add_edge(NodeName.APPROVAL.value, NodeName.EXECUTE.value)
builder.add_edge(NodeName.EXECUTE.value, END)

sub_goods_receipt_graph = builder.compile()
