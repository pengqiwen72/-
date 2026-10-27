from langgraph.graph import START, END, StateGraph
from graphs.ecom_graph.ecom_state import EcomState, InputState, OutputState, EcomConfig

from graphs.ecom_graph.ecom_sub_graph_profile.sub_profile_graph import sub_profile_graph
from graphs.ecom_graph.ecom_sub_graph_chat_suggestion.sub_chat_suggestion_graph import sub_chat_suggestion_graph
from graphs.ecom_graph.ecom_sub_graph_kf_chat_suggestion.sub_kf_chat_suggestion_graph import sub_kf_chat_suggestion_graph
from graphs.ecom_graph.ecom_sub_graph_tag.sub_tag_graph import sub_tag_graph
from graphs.ecom_graph.ecom_sub_graph_schedule.sub_schedule_graph import sub_schedule_graph
from graphs.ecom_graph.ecom_sub_graph_talk.sub_talk_graph import sub_talk_graph
from graphs.ecom_graph.ecom_node import NodeName, intent_detection, task_result_confirm, welcome_message, cleanup_state_node
from graphs.ecom_graph.ecom_node_load_data import NodeName as LoadDataNodeName
from graphs.ecom_graph.ecom_node_load_data import load_welcome_message_node, load_employee_info_node, load_tag_setting_node, load_customer_info_node, load_chat_history_node, load_kf_chat_history_node, load_order_history_node

import os
from dotenv import load_dotenv
load_dotenv()

builder = StateGraph(state_schema=EcomState, input_schema=InputState, output_schema=OutputState, config_schema=EcomConfig)

builder.add_node(NodeName.CLEANUP_STATE.value, cleanup_state_node) ## 清理状态
builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message) ## 欢迎消息
builder.add_node(NodeName.INTENT_DETECTION.value, intent_detection) ## 意图检测
builder.add_node(NodeName.TASK_RESULT_CONFIRM.value, task_result_confirm) ## 任务结果确认

## 加载数据节点
builder.add_node(LoadDataNodeName.LOAD_WELCOME_MESSAGE.value, load_welcome_message_node) ## 加载欢迎消息
builder.add_node(LoadDataNodeName.LOAD_EMPLOYEE_INFO.value, load_employee_info_node) ## 加载员工信息
builder.add_node(LoadDataNodeName.LOAD_TAG_SETTING.value, load_tag_setting_node) ## 加载标签设置
builder.add_node(LoadDataNodeName.LOAD_CUSTOMER_INFO.value, load_customer_info_node) ## 加载会员信息
builder.add_node(LoadDataNodeName.LOAD_CHAT_HISTORY.value, load_chat_history_node) ## 加载聊天消息
builder.add_node(LoadDataNodeName.LOAD_KF_CHAT_HISTORY.value, load_kf_chat_history_node) ## 加载微信客服信息
builder.add_node(LoadDataNodeName.LOAD_ORDER_HISTORY.value, load_order_history_node) ## 加载订单信息

## 生成建议节点
builder.add_node(NodeName.CHAT_SUGGESTION.value, sub_chat_suggestion_graph) ## 生成会员聊天建议
builder.add_node(NodeName.KF_CHAT_SUGGESTION.value, sub_kf_chat_suggestion_graph) ## 生成客服聊天建议
builder.add_node(NodeName.TAG_SUGGESTION.value, sub_tag_graph) ## 生成会员标签
builder.add_node(NodeName.PROFILE_SUGGESTION.value, sub_profile_graph) ## 生成买家画像
builder.add_node(NodeName.SCHEDULE_SUGGESTION.value, sub_schedule_graph) ## 生成会员日程
builder.add_node(NodeName.NO_CLEAR_INTENTION.value, sub_talk_graph) ## 未明确意图

## 加载数据节点执行完成后，调用意图检测节点
builder.add_edge(START, NodeName.CLEANUP_STATE.value)
builder.add_edge(NodeName.CLEANUP_STATE.value, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, LoadDataNodeName.LOAD_WELCOME_MESSAGE.value)
builder.add_edge(LoadDataNodeName.LOAD_WELCOME_MESSAGE.value, LoadDataNodeName.LOAD_EMPLOYEE_INFO.value)
builder.add_edge(LoadDataNodeName.LOAD_EMPLOYEE_INFO.value, LoadDataNodeName.LOAD_TAG_SETTING.value)
builder.add_edge(LoadDataNodeName.LOAD_TAG_SETTING.value, LoadDataNodeName.LOAD_CUSTOMER_INFO.value)
builder.add_edge(LoadDataNodeName.LOAD_CUSTOMER_INFO.value, LoadDataNodeName.LOAD_CHAT_HISTORY.value)
builder.add_edge(LoadDataNodeName.LOAD_CHAT_HISTORY.value, LoadDataNodeName.LOAD_KF_CHAT_HISTORY.value)
builder.add_edge(LoadDataNodeName.LOAD_KF_CHAT_HISTORY.value, LoadDataNodeName.LOAD_ORDER_HISTORY.value)
builder.add_edge(LoadDataNodeName.LOAD_ORDER_HISTORY.value, NodeName.INTENT_DETECTION.value)

## 意图检测后，根据意图调用对应的子图：
## 1. 如果意图是会员聊天建议，则调用sub_chat_suggestion_graph
## 2. 如果意图是客服聊天建议，则调用sub_kf_chat_suggestion_graph
## 3. 如果意图是会员标签，则调用sub_tag_graph
## 4. 如果意图是买家画像，则调用sub_profile_graph
## 5. 如果意图是会员日程，则调用sub_schedule_graph
## 6. 如果意图是未明确意图，则调用sub_talk_graph
## 子图执行完成后，调用NodeName.TASK_RESULT_CONFIRM节点


builder.add_edge(NodeName.CHAT_SUGGESTION.value,     NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.KF_CHAT_SUGGESTION.value,  NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.TAG_SUGGESTION.value,      NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.PROFILE_SUGGESTION.value,  NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.SCHEDULE_SUGGESTION.value, NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.NO_CLEAR_INTENTION.value,  NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.TASK_RESULT_CONFIRM.value, END)

if os.getenv("LANGFUSE_ENABLED", "false").lower() == "true":
    from langfuse.langchain import CallbackHandler
    langfuse_handler = CallbackHandler()
    graph = builder.compile().with_config({"callbacks": [langfuse_handler]})
else:
    graph = builder.compile()