"""
物料与制度问答（RAG）子图。

两个节点，一条直线：先发「正在检索」的提示，再检索并作答。
拆成两个节点不是为了好看——`welcome_message` 先跑，进度事件才有机会推出去，
前端在检索开始前就能显示状态；否则用户点完按钮要等到检索结束才有反馈。

双通道（文档检索 + 图谱关联）的通道选择与融合在 Phase 3 接入：
子图骨架不变，`retrieve_answer_node` 里换成带 channel 的检索入口即可。
"""

from langgraph.graph import END, START, StateGraph

from graphs.scm_graph.scm_state import ScmConfig
from graphs.scm_graph.sub_material_qa.sub_material_qa_node import (
    NodeName,
    retrieve_answer_node,
    welcome_message_node,
)
from graphs.scm_graph.sub_material_qa.sub_material_qa_state import (
    SubMaterialQAInputState,
    SubMaterialQAOutputState,
    SubMaterialQAState,
)

builder = StateGraph(
    state_schema=SubMaterialQAState,
    input_schema=SubMaterialQAInputState,
    output_schema=SubMaterialQAOutputState,
    config_schema=ScmConfig,
)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message_node)
builder.add_node(NodeName.RETRIEVE_ANSWER.value, retrieve_answer_node)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.RETRIEVE_ANSWER.value)
builder.add_edge(NodeName.RETRIEVE_ANSWER.value, END)

sub_material_qa_graph = builder.compile()
