from langgraph.graph import START, END, StateGraph
from graphs.ecom_graph.ecom_sub_graph_profile.sub_profile_state import SubProfileState, SubProfileInputState, SubProfileOutputState
from graphs.ecom_graph.ecom_sub_graph_profile.sub_profile_node import generate_customer_profile, update_customer_profile, human_feedback, notify_human_feedback, welcome_message, notify_human_result
from graphs.ecom_graph.ecom_sub_graph_profile.sub_profile_node import NodeName
from graphs.ecom_graph.ecom_state import EcomConfig

builder = StateGraph(state_schema=SubProfileState, input_schema=SubProfileInputState, output_schema=SubProfileOutputState, config_schema=EcomConfig)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message)
builder.add_node(NodeName.PROFILE_SUGGEST.value, generate_customer_profile)
builder.add_node(NodeName.PROFILE_UPDATE.value, update_customer_profile)
builder.add_node(NodeName.PROFILE_FEEDBACK.value, human_feedback)
builder.add_node(NodeName.PROFILE_NOTIFY_FEEDBACK.value, notify_human_feedback)
builder.add_node(NodeName.PROFILE_NOTIFY_RESULT.value, notify_human_result)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value) ## 欢迎消息节点
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.PROFILE_SUGGEST.value) ## 生成买家画像节点
builder.add_edge(NodeName.PROFILE_SUGGEST.value, NodeName.PROFILE_NOTIFY_FEEDBACK.value) ## 发送人工确认通知节点
builder.add_edge(NodeName.PROFILE_NOTIFY_FEEDBACK.value, NodeName.PROFILE_FEEDBACK.value) ## 人工反馈节点
## 人工反馈节点，如果人工反馈为ok，则跳转到profile_update节点，如果人工反馈为discard，则跳转到end，如果人工反馈为recreate，则跳转到profile_suggest节点。
builder.add_edge(NodeName.PROFILE_UPDATE.value, NodeName.PROFILE_NOTIFY_RESULT.value) ## 发送任务结果通知节点
builder.add_edge(NodeName.PROFILE_NOTIFY_RESULT.value, END) ## 结束节点

sub_profile_graph = builder.compile()