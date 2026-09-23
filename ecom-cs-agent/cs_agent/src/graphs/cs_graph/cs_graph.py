from langgraph.graph import START, END, StateGraph
from graphs.cs_graph.cs_state import CsState, InputState, OutputState, CsConfig

from graphs.cs_graph.sub_product_qa.sub_product_qa_graph import sub_product_qa_graph
from graphs.cs_graph.sub_order_query.sub_order_query_graph import sub_order_query_graph
from graphs.cs_graph.sub_after_sale.sub_after_sale_graph import sub_after_sale_graph
from graphs.cs_graph.sub_weekly_report.sub_weekly_report_graph import sub_weekly_report_graph
from graphs.cs_graph.sub_profile.sub_profile_graph import sub_profile_graph
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_graph import sub_chat_suggestion_graph
from graphs.cs_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_graph import sub_kf_chat_suggestion_graph
from graphs.cs_graph.sub_tag.sub_tag_graph import sub_tag_graph
from graphs.cs_graph.sub_schedule.sub_schedule_graph import sub_schedule_graph
from graphs.cs_graph.sub_talk.sub_talk_graph import sub_talk_graph
from graphs.cs_graph.cs_node import NodeName, intent_detection, task_result_confirm, welcome_message, cleanup_state_node
from graphs.cs_graph.cs_node_load_data import NodeName as LoadDataNodeName
from graphs.cs_graph.cs_node_load_data import load_welcome_message_node, load_employee_info_node, load_tag_setting_node, load_customer_info_node, load_chat_history_node, load_kf_chat_history_node, load_order_history_node

import os
from dotenv import load_dotenv
load_dotenv()

from utils.agent_logger import get_logger

logger = get_logger("cs_graph")

builder = StateGraph(state_schema=CsState, input_schema=InputState, output_schema=OutputState, config_schema=CsConfig)

builder.add_node(NodeName.CLEANUP_STATE.value, cleanup_state_node) ## 清理状态
builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message) ## 欢迎消息
builder.add_node(NodeName.INTENT_DETECTION.value, intent_detection) ## 意图检测
builder.add_node(NodeName.TASK_RESULT_CONFIRM.value, task_result_confirm) ## 任务结果确认

## 加载数据节点
builder.add_node(LoadDataNodeName.LOAD_WELCOME_MESSAGE.value, load_welcome_message_node) ## 加载欢迎消息
builder.add_node(LoadDataNodeName.LOAD_EMPLOYEE_INFO.value, load_employee_info_node) ## 加载员工信息
builder.add_node(LoadDataNodeName.LOAD_TAG_SETTING.value, load_tag_setting_node) ## 加载标签设置
builder.add_node(LoadDataNodeName.LOAD_CUSTOMER_INFO.value, load_customer_info_node) ## 加载客户信息
builder.add_node(LoadDataNodeName.LOAD_CHAT_HISTORY.value, load_chat_history_node) ## 加载聊天消息
builder.add_node(LoadDataNodeName.LOAD_KF_CHAT_HISTORY.value, load_kf_chat_history_node) ## 加载微信客服信息
builder.add_node(LoadDataNodeName.LOAD_ORDER_HISTORY.value, load_order_history_node) ## 加载订单信息

## 生成建议节点
builder.add_node(NodeName.PRODUCT_QA.value, sub_product_qa_graph) ## 商品咨询问答（RAG）
builder.add_node(NodeName.ORDER_QUERY.value, sub_order_query_graph) ## 订单查询（多 API 编排）
builder.add_node(NodeName.AFTER_SALE.value, sub_after_sale_graph) ## 售后处理（政策匹配 + HITL 审批 + 幂等）
builder.add_node(NodeName.WEEKLY_REPORT.value, sub_weekly_report_graph) ## 数据周报（确定性算数 + 模型只写摘要）
builder.add_node(NodeName.CHAT_SUGGESTION.value, sub_chat_suggestion_graph) ## 生成客户聊天建议
builder.add_node(NodeName.KF_CHAT_SUGGESTION.value, sub_kf_chat_suggestion_graph) ## 生成客服聊天建议
builder.add_node(NodeName.TAG_SUGGESTION.value, sub_tag_graph) ## 生成客户标签
builder.add_node(NodeName.PROFILE_SUGGESTION.value, sub_profile_graph) ## 生成客户画像
builder.add_node(NodeName.SCHEDULE_SUGGESTION.value, sub_schedule_graph) ## 生成客户日程
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
## 1. 如果意图是商品咨询，则调用sub_product_qa_graph
## 2. 如果意图是客户聊天建议，则调用sub_chat_suggestion_graph
## 3. 如果意图是客服聊天建议，则调用sub_kf_chat_suggestion_graph
## 4. 如果意图是客户标签，则调用sub_tag_graph
## 5. 如果意图是客户画像，则调用sub_profile_graph
## 6. 如果意图是客户日程，则调用sub_schedule_graph
## 7. 如果意图是未明确意图，则调用sub_talk_graph
## 子图执行完成后，调用NodeName.TASK_RESULT_CONFIRM节点


builder.add_edge(NodeName.PRODUCT_QA.value,          NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.ORDER_QUERY.value,         NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.AFTER_SALE.value,          NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.WEEKLY_REPORT.value,       NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.CHAT_SUGGESTION.value,     NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.KF_CHAT_SUGGESTION.value,  NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.TAG_SUGGESTION.value,      NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.PROFILE_SUGGESTION.value,  NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.SCHEDULE_SUGGESTION.value, NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.NO_CLEAR_INTENTION.value,  NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.TASK_RESULT_CONFIRM.value, END)

def _get_callbacks():
    """按配置返回链路追踪回调（未启用则返回空列表）。"""
    if os.getenv("LANGFUSE_ENABLED", "false").lower() == "true":
        try:
            from langfuse.langchain import CallbackHandler

            return [CallbackHandler()]
        except Exception as e:  # pragma: no cover - 追踪不可用不应阻断主流程
            logger.warning(f"Langfuse 回调初始化失败，跳过追踪：{e}")
    return []


def build_graph(*, checkpointer=None, store=None):
    """
    编译主图。

    显式传入 checkpointer / store 时，图可以在**脱离 LangGraph 平台运行时**的
    场景下工作（离线 demo、pytest）。不传时保持原有行为，由平台注入。

    平台模式说明：langgraph dev 会在装载时用服务端自己的 checkpointer/store
    覆盖图上的实例，所以这里给模块级 graph 挂上 InMemory 实现不会影响线上。
    """
    compiled = builder.compile(checkpointer=checkpointer, store=store)

    callbacks = _get_callbacks()
    if callbacks:
        compiled = compiled.with_config({"callbacks": callbacks})
    return compiled


# 模块级图：供 langgraph.json 注册（平台运行时）
graph = build_graph()