from langchain_core.runnables import RunnableConfig
from llm.llm_suggest_schedule import llm_schedule_suggest
from tools.wechat_tool import WxWorkAPI
from graphs.ecom_graph.ecom_sub_graph_schedule.sub_schedule_state import SubScheduleState, SubScheduleStateField
from utils.agent_logger import get_logger
from graphs.ecom_graph.ecom_state import ConfigurableField
from datetime import datetime
from enum import Enum
from models.ecom_models import CustomerInfo, ChatHistory, ScheduleSuggestion, TaskResult, NodeResult


logger = get_logger("sub_schedule_node")


class NodeName(str, Enum):
    WELCOME_MESSAGE   = "schedule_welcome_node"
    GENERATE_SCHEDULE = "schedule_generate_node"
    CREATE_SCHEDULE   = "schedule_create_node"

def welcome_message_node(state: SubScheduleState, config: RunnableConfig):
    """欢迎消息节点"""
    
    logger.info("=== 欢迎消息 ===")
    
    return {
        SubScheduleStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在为您生成日程建议，请稍等。",
            execute_exceptions=[]
        )]
    }


def generate_schedule_node(state: SubScheduleState, config: RunnableConfig):
    """生成日程节点"""
    
    logger.info("=== 生成日程 ===")

    try:
        generated_schedule_suggestion = llm_schedule_suggest(
            customer_info = state.get(SubScheduleStateField.CUSTOMER_INFO, CustomerInfo()),
            chat_history = state.get(SubScheduleStateField.CHAT_HISTORY, ChatHistory()),
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        logger.info(f"generated_schedule_suggestion: {generated_schedule_suggestion}")

        return {
            SubScheduleStateField.SUGGESTION_SCHEDULE: generated_schedule_suggestion,
            SubScheduleStateField.TASK_RESULT: TaskResult(
                task_result=f"生成日程建议成功：\n{generated_schedule_suggestion.model_dump_json()}",
                task_result_explain=f"生成日程建议成功",
                task_result_code=0
            ),
            SubScheduleStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_SCHEDULE.value,
                execute_result_code=0,
                execute_result_msg="生成日程建议成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"生成日程建议失败: {e}")
        return {
            SubScheduleStateField.SUGGESTION_SCHEDULE: ScheduleSuggestion(),
            SubScheduleStateField.TASK_RESULT: TaskResult(
                task_result=f"生成日程建议失败",
                task_result_explain=f"生成日程建议失败",
                task_result_code=1
            ),
            SubScheduleStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_SCHEDULE.value,
                execute_result_code=1,
                execute_result_msg=f"生成日程建议失败: {e}",
                execute_exceptions=[str(e)]
            )]
        }


def create_schedule_node(state: SubScheduleState, config: RunnableConfig):
    """创建日程节点"""
    
    logger.info("=== 创建日程 ===")

    wxwork_api = WxWorkAPI()

    user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]

    # 创建企业微信日程
    try:

        schedule = state.get(SubScheduleStateField.SUGGESTION_SCHEDULE, ScheduleSuggestion())
        logger.info(f"schedule: {schedule.model_dump_json()}")

        title = schedule.title
        start = schedule.start_time
        duration = schedule.duration

        logger.info(f"title: {title}")
        logger.info(f"start: {start}")
        logger.info(f"duration: {duration}")

        if not title or not start:
            return {
                SubScheduleStateField.TASK_RESULT: TaskResult(
                    task_result=f"日程标题或开始时间不能为空",
                    task_result_explain=f"日程标题或开始时间不能为空",
                    task_result_code=1
                ),
                SubScheduleStateField.NODE_RESULT: [NodeResult(
                    execute_node_name=NodeName.CREATE_SCHEDULE.value,
                    execute_result_code=1,
                    execute_result_msg=f"日程标题或开始时间不能为空",
                    execute_exceptions=[]
                )]
            }

        result = wxwork_api.create_schedule(user_id = user_id, title = title, start_time = start, duration_minutes = duration)
        logger.info(f"日程创建成功: {result}")
        
        return {
            SubScheduleStateField.TASK_RESULT: TaskResult(
                task_result=f"创建日程成功",
                task_result_explain=f"创建日程成功",
                task_result_code=0
            ),
            SubScheduleStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.CREATE_SCHEDULE.value,
                execute_result_code=0,
                execute_result_msg=f"创建日程成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"创建日程失败: {e}")
        return {
            SubScheduleStateField.TASK_RESULT: TaskResult(
                task_result=f"创建日程失败: {e}",
                task_result_explain=f"创建日程失败",
                task_result_code=1
            ),
            SubScheduleStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.CREATE_SCHEDULE.value,
                execute_result_code=1,
                execute_result_msg=f"创建日程失败: {e}",
                execute_exceptions=[str(e)]
            )]
        }

