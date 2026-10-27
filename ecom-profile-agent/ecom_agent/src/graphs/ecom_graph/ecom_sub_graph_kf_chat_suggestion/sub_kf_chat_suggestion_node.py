from graphs.ecom_graph.ecom_sub_graph_kf_chat_suggestion.sub_kf_chat_suggestion_state import SubKFChatSuggestionState
from graphs.ecom_graph.ecom_sub_graph_kf_chat_suggestion.sub_kf_chat_suggestion_state import SubKFChatSuggestionStateField
from models.ecom_models import CustomerInfo, KFChatHistory, TaskResult, ReplySuggestion, NodeResult
from utils.agent_logger import get_logger
from langchain_core.runnables import RunnableConfig
from llm.llm_suggest_kf_chat import llm_kf_chat_suggest
from datetime import datetime
from enum import Enum

logger = get_logger("sub_kf_chat_suggestion_node")

class NodeName(str, Enum):
    WELCOME_MESSAGE             = "kf_chat_welcome_node"
    GENERATE_KF_CHAT_SUGGESTION = "kf_chat_generate_node"

def welcome_message_node(state: SubKFChatSuggestionState, config: RunnableConfig):
    """欢迎信息节点"""
    
    logger.info("=== 欢迎信息 ===")
    
    return {
        SubKFChatSuggestionStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在为您生成客服回复建议，请稍等。",
            execute_exceptions=[]
        )]
    }


def generate_kf_chat_suggestion_node(state: SubKFChatSuggestionState, config: RunnableConfig):
    """生成客服回复建议节点"""
    
    logger.info("=== 生成客服回复建议 ===")
    logger.debug(f"config: {config}")

    try:

        kf_chat_suggestion: ReplySuggestion = llm_kf_chat_suggest(
            customer_info = state.get(SubKFChatSuggestionStateField.CUSTOMER_INFO, CustomerInfo()),
            kf_chat_history = state.get(SubKFChatSuggestionStateField.KF_CHAT_HISTORY, KFChatHistory()),
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        return {
            SubKFChatSuggestionStateField.SUGGESTION_KF: kf_chat_suggestion,
            SubKFChatSuggestionStateField.TASK_RESULT: TaskResult(
                task_result=kf_chat_suggestion.reply_content,
                task_result_explain=kf_chat_suggestion.reply_reason,
                task_result_code=0
            ),
            SubKFChatSuggestionStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_KF_CHAT_SUGGESTION.value,
                execute_result_code=0,
                execute_result_msg="生成客服回复建议成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"生成客服回复建议失败: {e}")
        return {
            SubKFChatSuggestionStateField.SUGGESTION_KF: ReplySuggestion(),
            SubKFChatSuggestionStateField.TASK_RESULT: TaskResult(
                task_result=f"很抱歉，生成客服回复建议过程中，遇到了问题。",
                task_result_explain=f"生成客服回复建议失败",
                task_result_code=1
            ),
            SubKFChatSuggestionStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_KF_CHAT_SUGGESTION.value,
                execute_result_code=1,
                execute_result_msg=f"生成客服回复建议失败",
                execute_exceptions=[str(e)]
            )]
        }

