"""
库存管理（多仓分布 / 安全库存预警）子图。

两个节点，一条直线：先报「正在汇总」，再聚合出报告。
拆两个节点是为了让进度事件在聚合开始前就能推出去（与问答链路同一个理由）。
"""

from langgraph.graph import END, START, StateGraph

from graphs.scm_graph.scm_state import ScmConfig
from graphs.scm_graph.sub_inventory.sub_inventory_node import (
    NodeName,
    query_node,
    welcome_node,
)
from graphs.scm_graph.sub_inventory.sub_inventory_state import (
    SubInventoryInputState,
    SubInventoryOutputState,
    SubInventoryState,
)

builder = StateGraph(
    state_schema=SubInventoryState,
    input_schema=SubInventoryInputState,
    output_schema=SubInventoryOutputState,
    config_schema=ScmConfig,
)

builder.add_node(NodeName.WELCOME.value, welcome_node)
builder.add_node(NodeName.QUERY.value, query_node)

builder.add_edge(START, NodeName.WELCOME.value)
builder.add_edge(NodeName.WELCOME.value, NodeName.QUERY.value)
builder.add_edge(NodeName.QUERY.value, END)

sub_inventory_graph = builder.compile()
