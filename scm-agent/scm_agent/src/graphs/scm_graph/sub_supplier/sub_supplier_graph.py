"""
供应商管理（比价 / 履约评分排名）子图。

    welcome → resolve → report → END

三段直线：报进度 → 定物料 → 算排名。定位与算分拆开，是因为「没定位到物料」
必须在算分之前就拦下来 —— 算分节点拿不到物料时只该是意外（防御性分支），
而不是一条正常的追问路径。
"""

from langgraph.graph import END, START, StateGraph

from graphs.scm_graph.scm_state import ScmConfig
from graphs.scm_graph.sub_supplier.sub_supplier_node import (
    NodeName,
    report_node,
    resolve_node,
    welcome_node,
)
from graphs.scm_graph.sub_supplier.sub_supplier_state import (
    SubSupplierInputState,
    SubSupplierOutputState,
    SubSupplierState,
)

builder = StateGraph(
    state_schema=SubSupplierState,
    input_schema=SubSupplierInputState,
    output_schema=SubSupplierOutputState,
    config_schema=ScmConfig,
)

builder.add_node(NodeName.WELCOME.value, welcome_node)
builder.add_node(NodeName.RESOLVE.value, resolve_node)
builder.add_node(NodeName.REPORT.value, report_node)

builder.add_edge(START, NodeName.WELCOME.value)
builder.add_edge(NodeName.WELCOME.value, NodeName.RESOLVE.value)
builder.add_edge(NodeName.RESOLVE.value, NodeName.REPORT.value)
builder.add_edge(NodeName.REPORT.value, END)

sub_supplier_graph = builder.compile()
