"""
数据周报子图。

    collect（取数）→ aggregate（算数）→ narrate（说话）→ END

线性三段，没有分支：周报没有「需要人工确认」的环节，可选的降级（模型输出
不合规、上游取数失败）都已经在节点内部处理掉了 —— 它们改变的是**内容**
而不是**流程**，所以不该在图上多出几条边。图越简单，越容易让人一眼看明白
「哪一段在算数、哪一段在说话」。
"""

from langgraph.graph import START, END, StateGraph

from graphs.scm_graph.sub_weekly_report.sub_weekly_report_node import (
    NodeName,
    aggregate_node,
    collect_node,
    narrate_node,
)
from graphs.scm_graph.sub_weekly_report.sub_weekly_report_state import (
    SubWeeklyReportInputState,
    SubWeeklyReportOutputState,
    SubWeeklyReportState,
)

builder = StateGraph(
    state_schema=SubWeeklyReportState,
    input_schema=SubWeeklyReportInputState,
    output_schema=SubWeeklyReportOutputState,
)

builder.add_node(NodeName.COLLECT.value, collect_node)
builder.add_node(NodeName.AGGREGATE.value, aggregate_node)
builder.add_node(NodeName.NARRATE.value, narrate_node)

builder.add_edge(START, NodeName.COLLECT.value)
builder.add_edge(NodeName.COLLECT.value, NodeName.AGGREGATE.value)
builder.add_edge(NodeName.AGGREGATE.value, NodeName.NARRATE.value)
builder.add_edge(NodeName.NARRATE.value, END)

sub_weekly_report_graph = builder.compile()
