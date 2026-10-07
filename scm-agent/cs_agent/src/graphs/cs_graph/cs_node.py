from enum import Enum

from typing_extensions import Literal
from langgraph.types import Command
from utils.agent_logger import get_logger
from langchain_core.runnables import RunnableConfig
from graphs.cs_graph.cs_state import CsState, CsStateField
from models.cs_models import Intent, NodeResult
from llm.llm_intent_detect import llm_intent_detect

logger = get_logger("cs_node")

# 节点名称
class NodeName(str, Enum):
    # 主图节点
    CLEANUP_STATE       = "cleanup_state"
    WELCOME_MESSAGE     = "cs_welcome_message"
    INTENT_DETECTION    = "intent_detection"
    TASK_RESULT_CONFIRM = "task_result_confirm"

    # 子图节点（意图识别后，根据意图调用对应的子图）
    PRODUCT_QA          = "product_qa" ## 商品咨询问答（RAG）
    ORDER_QUERY         = "order_query" ## 订单查询（多 API 编排）
    AFTER_SALE          = "after_sale" ## 售后处理（政策引擎 + HITL + 幂等）
    WEEKLY_REPORT       = "weekly_report" ## 运营数据周报
    CHAT_SUGGESTION     = "chat_suggestion" ## 生成客户聊天建议
    KF_CHAT_SUGGESTION  = "kf_chat_suggestion" ## 生成客服聊天建议
    TAG_SUGGESTION      = "tag_suggestion" ## 生成客户标签
    PROFILE_SUGGESTION  = "profile_suggestion" ## 生成客户画像
    SCHEDULE_SUGGESTION = "schedule_suggestion" ## 生成客户日程
    NO_CLEAR_INTENTION  = "no_clear_intention" ## 未明确意图

# 意图列表（意图识别后，根据意图调用对应的子图）
class IntentDetection(str, Enum):
    PRODUCT_QA          = NodeName.PRODUCT_QA.value
    ORDER_QUERY         = NodeName.ORDER_QUERY.value
    AFTER_SALE          = NodeName.AFTER_SALE.value
    WEEKLY_REPORT       = NodeName.WEEKLY_REPORT.value
    CHAT_SUGGESTION     = NodeName.CHAT_SUGGESTION.value
    KF_CHAT_SUGGESTION  = NodeName.KF_CHAT_SUGGESTION.value
    TAG_SUGGESTION      = NodeName.TAG_SUGGESTION.value
    PROFILE_SUGGESTION  = NodeName.PROFILE_SUGGESTION.value
    SCHEDULE_SUGGESTION = NodeName.SCHEDULE_SUGGESTION.value
    NO_CLEAR_INTENTION  = NodeName.NO_CLEAR_INTENTION.value

def cleanup_state_node(state: CsState, config: RunnableConfig):
    """清理状态节点"""
    logger.info("=== 清理状态 ===")
    return {
        ## 执行结果
        CsStateField.NODE_RESULT: None,
        CsStateField.TASK_RESULT: None,
        ## 信息Load
        CsStateField.EMPLOYEE_INFO: None,
        CsStateField.CHAT_HISTORY: None,
        CsStateField.KF_CHAT_HISTORY: None,
        CsStateField.ORDER_HISTORY: None,
        CsStateField.TAG_SETTING: None,
        CsStateField.CUSTOMER_INFO: None,
        CsStateField.CUSTOMER_TAGS: None,
        CsStateField.CUSTOMER_PROFILE: None,
        ## Intermediate Output 中间输出
        CsStateField.SUGGESTION_PROFILE: None,
        CsStateField.SUGGESTION_TAG: None,
        CsStateField.SUGGESTION_CHAT: None,
        CsStateField.SUGGESTION_KF: None,
        CsStateField.SUGGESTION_SCHEDULE: None,
        CsStateField.NOTIFY_CONTENT: None,
        CsStateField.PRODUCT_QA: None,
        CsStateField.ORDER_QUERY: None,
        CsStateField.AFTER_SALE: None,
        CsStateField.WEEKLY_REPORT: None,
    }

def welcome_message(state: CsState, config: RunnableConfig):
    """欢迎消息节点"""
    
    logger.info("=== 欢迎消息 ===")
    
    return {
        CsStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="您好，我是电商智能客服助手，很高兴为您服务",
            execute_exceptions=[]
        )]
    }

def intent_detection(state: CsState, config: RunnableConfig) -> Command[Literal[*[intent.value for intent in IntentDetection]]]:
    """意图检测节点"""
    
    logger.info("=== 意图检测 ===")
    
    intents=[
        Intent(intent_id=IntentDetection.PRODUCT_QA.value, intent_description="客户在问商品参数、使用方法、售后政策、运费包邮等具体问题，需要基于商品知识库给出有依据的答复"),
        Intent(intent_id=IntentDetection.ORDER_QUERY.value, intent_description="需要查询订单状态、物流到哪了、什么时候能到、某个商品还有没有库存"),
        Intent(intent_id=IntentDetection.AFTER_SALE.value, intent_description="客户要申请退款、退货、换货，反馈商品破损或有质量问题，需要按售后政策处理"),
        Intent(intent_id=IntentDetection.WEEKLY_REPORT.value, intent_description="需要一份运营数据统计：周报、订单量/成交额/退款率等指标的同环比"),
        Intent(intent_id=IntentDetection.CHAT_SUGGESTION.value, intent_description="我是销售人员，正在和客户沟通，我需要知道如何回复比较合适"),
        Intent(intent_id=IntentDetection.KF_CHAT_SUGGESTION.value, intent_description="我是企业客服，在帮助客户解决问题，请帮助我生成客服回复建议"),
        Intent(intent_id=IntentDetection.TAG_SUGGESTION.value, intent_description="我的客户情况有更新，我需要更客户标签"),
        Intent(intent_id=IntentDetection.PROFILE_SUGGESTION.value, intent_description="我的客户情况有更新，我需要更新客户画像"),
        Intent(intent_id=IntentDetection.SCHEDULE_SUGGESTION.value, intent_description="我和客户沟通过程中，我需要创建日程，方便提醒我跟进事项"),
        Intent(intent_id=IntentDetection.NO_CLEAR_INTENTION.value, intent_description="我想咨询一些问题，或者没有明确的意图，仅仅是聊聊天")
    ]

    task_input = state.get(CsStateField.TASK_INPUT, "")

    # 第一层：枚举精确匹配 —— 任务值直接等于意图名，零成本直达，不调模型
    if task_input in [intent.value for intent in IntentDetection]:
        logger.info(f"意图枚举命中: {task_input}")
        return Command(goto=task_input)

    # 第二层：语义识别 —— 自然语言描述交给模型判断
    intent_id = IntentDetection.NO_CLEAR_INTENTION.value
    try:
        intent = llm_intent_detect(task_input, intents)
        intent_id = intent.intent_id
    except Exception as e:
        logger.warning(f"语义意图识别失败，回落未明确意图: {e}")

    # 第三层：兜底 —— 模型给了不在枚举里的值，同样归入通用问答
    if not intent_id in [intent.value for intent in IntentDetection]:
        intent_id = IntentDetection.NO_CLEAR_INTENTION.value

    logger.info(f"意图识别结果: {intent_id}")
    return Command(goto = intent_id)


# 任务结果确认
def task_result_confirm(state: CsState, config: RunnableConfig):
    """任务结果确认节点"""
    
    logger.info("=== 任务结果确认 ===")

    return {
        CsStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.TASK_RESULT_CONFIRM.value,
            execute_result_code=0,
            execute_result_msg="任务已完成，希望您能满意",
            execute_exceptions=[]
        )]
    }