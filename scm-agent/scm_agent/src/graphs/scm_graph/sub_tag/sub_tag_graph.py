from langgraph.graph import START, END, StateGraph
from graphs.scm_graph.sub_tag.sub_tag_state import SubTagState, SubTagInputState, SubTagOutputState
from graphs.scm_graph.sub_tag.sub_tag_node import generate_customer_tag, update_customer_tag, human_feedback, welcome_message_node, notify_human_feedback, notify_human_result
from graphs.scm_graph.sub_tag.sub_tag_node import NodeName
from graphs.scm_graph.scm_state import ScmConfig

builder = StateGraph(state_schema=SubTagState, input_schema=SubTagInputState, output_schema=SubTagOutputState, config_schema=ScmConfig)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message_node)
builder.add_node(NodeName.GENERATE_TAG.value, generate_customer_tag)
builder.add_node(NodeName.NOTIFY_FEEDBACK.value, notify_human_feedback)
builder.add_node(NodeName.HUMAN_FEEDBACK.value, human_feedback)
builder.add_node(NodeName.UPDATE_TAG.value, update_customer_tag)
builder.add_node(NodeName.NOTIFY_RESULT.value, notify_human_result)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value) ## 欢迎消息节点
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.GENERATE_TAG.value) ## 生成客户标签节点
builder.add_edge(NodeName.GENERATE_TAG.value, NodeName.NOTIFY_FEEDBACK.value) ## 发送人工确认通知节点
builder.add_edge(NodeName.NOTIFY_FEEDBACK.value, NodeName.HUMAN_FEEDBACK.value) ## 人工反馈节点
# 人工反馈节点，如果人工反馈为ok，则跳转到update_tag节点，如果人工反馈为discard，则跳转到end，如果人工反馈为recreate，则跳转到generate_tag节点。
builder.add_edge(NodeName.UPDATE_TAG.value, NodeName.NOTIFY_RESULT.value) ## 发送任务结果通知节点
builder.add_edge(NodeName.NOTIFY_RESULT.value, END) ## 结束节点

sub_tag_graph = builder.compile()