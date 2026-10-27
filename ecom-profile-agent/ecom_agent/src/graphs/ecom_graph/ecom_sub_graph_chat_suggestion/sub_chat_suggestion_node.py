from graphs.ecom_graph.ecom_sub_graph_chat_suggestion.sub_chat_suggestion_state import SubChatSuggestionState
from graphs.ecom_graph.ecom_sub_graph_chat_suggestion.sub_chat_suggestion_state import SubChatSuggestionStateField
from models.ecom_models import CustomerInfo, EmployeeInfo, ChatHistory, ReplySuggestion, TaskResult, NodeResult
from utils.agent_logger import get_logger
from langchain_core.runnables import RunnableConfig
from llm.llm_suggest_chat import llm_chat_suggest
from datetime import datetime
from enum import Enum


logger = get_logger("sub_chat_suggestion_node")

class NodeName(str, Enum):
    WELCOME_MESSAGE          = "chat_welcome_node"
    GENERATE_CHAT_SUGGESTION = "chat_generate_node"

def welcome_message_node(state: SubChatSuggestionState, config: RunnableConfig):
    """欢迎信息节点"""
    
    logger.info("=== 欢迎信息 ===")
    
    return {
        SubChatSuggestionStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在为您生成聊天建议，请稍等。",
            execute_exceptions=[]
        )]
    }

def generate_chat_suggestion_node(state: SubChatSuggestionState, config: RunnableConfig):
    """生成聊天建议节点"""
    
    logger.info("=== 生成聊天建议 ===")
    logger.debug(f"config: {config}")

    try:

        chat_suggestion: ReplySuggestion = llm_chat_suggest(
            customer_info = state.get(SubChatSuggestionStateField.CUSTOMER_INFO, CustomerInfo()),
            employee_info = state.get(SubChatSuggestionStateField.EMPLOYEE_INFO, EmployeeInfo()),
            chat_history = state.get(SubChatSuggestionStateField.CHAT_HISTORY, ChatHistory()),
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )   

        return {
            SubChatSuggestionStateField.SUGGESTION_CHAT: chat_suggestion,
            SubChatSuggestionStateField.TASK_RESULT: TaskResult(
                task_result=chat_suggestion.reply_content,
                task_result_explain=chat_suggestion.reply_reason,
                task_result_code=0
            ),
            SubChatSuggestionStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_CHAT_SUGGESTION.value,
                execute_result_code=0,
                execute_result_msg="生成聊天建议成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"生成聊天建议失败: {e}")
        return {
            SubChatSuggestionStateField.SUGGESTION_CHAT: ReplySuggestion(),
            SubChatSuggestionStateField.TASK_RESULT: TaskResult(
                task_result=f"很抱歉，生成聊天建议过程中，遇到了问题。",
                task_result_explain=f"生成聊天建议失败",
                task_result_code=1
            ),
            SubChatSuggestionStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_CHAT_SUGGESTION.value,
                execute_result_code=1,
                execute_result_msg=f"生成聊天建议失败",
                execute_exceptions=[str(e)]
            )]
        }

