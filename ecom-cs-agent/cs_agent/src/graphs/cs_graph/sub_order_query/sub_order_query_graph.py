"""订单查询子图：一个提示节点 + 一个取数节点，线性结构。"""

from langgraph.graph import START, END, StateGraph

from graphs.cs_graph.sub_order_query.sub_order_query_node import NodeName, fetch_node, welcome_node
from graphs.cs_graph.sub_order_query.sub_order_query_state import (
    SubOrderQueryInputState,
    SubOrderQueryOutputState,
    SubOrderQueryState,
)

builder = StateGraph(
    state_schema=SubOrderQueryState,
    input_schema=SubOrderQueryInputState,
    output_schema=SubOrderQueryOutputState,
)

builder.add_node(NodeName.WELCOME.value, welcome_node)
builder.add_node(NodeName.FETCH.value, fetch_node)

builder.add_edge(START, NodeName.WELCOME.value)
builder.add_edge(NodeName.WELCOME.value, NodeName.FETCH.value)
builder.add_edge(NodeName.FETCH.value, END)

sub_order_query_graph = builder.compile()
