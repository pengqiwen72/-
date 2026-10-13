"""
供应商管理（比价排名 / 履约评估）子图。

    welcome → resolve ─┬─(失败)─→ END
                       └─(其余)─→ report → END

定位与出报告拆开，是因为「没定位到目标」必须在出报告之前就拦下来 ——
报告节点拿不到目标时只该是意外（防御性分支），而不是一条正常的追问路径。
失败路由到 END 与采购链路同型：追问话术由 resolve 写好，report 不必也不该
再失败一次（曾经是直线边，轨迹上会出现两条重复的 ✗，第二条毫无信息）。

判据用 resolve 节点写下的 `NodeResult.execute_result_code`：比价/评估报告是
只读产物模型（没有 status 字段，也不该为路由加一个），而失败信号本来就由
这个 code 表达 —— 时间线上的 ✗ 与这里的路由读的是同一处，不会各说各话。
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
    SubSupplierStateField,
)


def _route_after_resolve(state: SubSupplierState) -> str:
    """定位失败直接结束，其余去出报告。"""
    results = state.get(SubSupplierStateField.NODE_RESULT) or []
    if results and results[-1].execute_result_code != 0:
        return END
    return NodeName.REPORT.value


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
builder.add_conditional_edges(
    NodeName.RESOLVE.value,
    _route_after_resolve,
    {
        NodeName.REPORT.value: NodeName.REPORT.value,
        END: END,
    },
)
builder.add_edge(NodeName.REPORT.value, END)

sub_supplier_graph = builder.compile()
