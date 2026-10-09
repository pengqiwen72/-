"""
收货验收子图。

    welcome → match ─┬─(不良率超阈值)─→ approval ─┬─(改判重报且仍需复核)─→ approval（循环）
                     │                            ├─(失败)─────────────→ END
                     │                            └─(其余)─────────────→ execute → END
                     ├─(失败)──────────────────────────────────────────→ END
                     └─(其余)───────────────────────────────────────────→ execute → END

与采购申请同型：复核只是多一跳，执行永远只有一处。
patch 改不了不良率这个事实，所以收货侧的 recreate 复判必然仍命中 QC-001 ——
approval 的自回路在这里不是「可能走到」，而是改判重报的**必经之路**：
第二轮复核卡上带着改后的处置方式，复核人确认后按它执行。
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
    """超阈值先复核，失败直接结束，其余执行。

    判据用 `status == "pending_review"`（理由同采购子图：`need_review` 只是
    策略属性，降级分支产出的方案也是 False）。`failed`（登记缺要素、策略库
    不可用）必须路由到 END —— 失败方案没有可登记的数量，流过执行节点会拿
    空数值去入库。
    """
    plan = state.get(SubGoodsReceiptStateField.GOODS_RECEIPT)
    if plan is not None and plan.status == "failed":
        return END
    if plan is not None and plan.status == "pending_review" and plan.need_review:
        return NodeName.APPROVAL.value
    return NodeName.EXECUTE.value


def _route_after_approval(state: SubGoodsReceiptState) -> str:
    """复核后：复判仍需复核回到复核（recreate 第二轮的落点），失败结束，其余执行。

    recreate 后 QC-001 必然仍命中（不良率是事实，patch 改不了），所以改判
    重报在这里总会再中断一轮 —— 第二轮卡片上带着改后的处置方式，
    复核通过（approve）时按它执行（节点侧 `approval_node` 保留 patch 值）。
    """
    plan = state.get(SubGoodsReceiptStateField.GOODS_RECEIPT)
    if plan is not None and plan.status == "failed":
        return END
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

sub_goods_receipt_graph = builder.compile()
