import json
from enum import Enum
from typing_extensions import Literal
from graphs.cs_graph.cs_state import ConfigurableField
from langgraph.types import interrupt, Command
from langgraph.graph import END
from utils.agent_logger import get_logger
from llm.llm_setting import chat_model as llm
from llm.llm_suggest_profile import llm_profile_suggest
from integrations.factory import get_notify_port
from models.cs_models import CustomerProfile, NodeResult, ChatHistory, KFChatHistory, OrderHistory, CustomerTags
from graphs.cs_graph.sub_profile.sub_profile_state import SubProfileState, SubProfileStateField, TaskResult
from langchain_core.runnables import RunnableConfig
from tools.store_tool import update_customer_profile as update_customer_profile_tool # 方法重名，需要使用别名


logger = get_logger("sub_profile_node")


class HumanFeedback(str, Enum):
    OK          = "ok"
    DISCARD     = "discard"
    RECREATE    = "recreate"
    FIELD_NAME  = "confirmed"

class NodeName(str, Enum):
    WELCOME_MESSAGE         = "profile_welcome_node"
    PROFILE_SUGGEST         = "profile_suggest_node"
    PROFILE_UPDATE          = "profile_update_node"
    PROFILE_FEEDBACK        = "profile_feedback_node"
    PROFILE_NOTIFY_FEEDBACK = "profile_notify_feedback_node"
    PROFILE_NOTIFY_RESULT   = "profile_notify_result_node"


def welcome_message(state: SubProfileState, config: RunnableConfig):
    """欢迎消息节点"""

    logger.info("=== 欢迎消息 ===")

    return {
        SubProfileStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在生成客户画像，请稍等...",
            execute_exceptions=[]
        )]
    }



def generate_customer_profile(state: SubProfileState, config: RunnableConfig):
    """生成客户Profile"""
    
    logger.info("=== 生成客户profile ===")

    try:
        generated_profile: CustomerProfile = llm_profile_suggest(
            chat_history=state.get(SubProfileStateField.CHAT_HISTORY, ChatHistory()),
            kf_chat_history=state.get(SubProfileStateField.KF_CHAT_HISTORY, KFChatHistory()),
            order_history=state.get(SubProfileStateField.ORDER_HISTORY, OrderHistory()),
            customer_tags=state.get(SubProfileStateField.CUSTOMER_TAGS, CustomerTags()),
            customer_profile=state.get(SubProfileStateField.CUSTOMER_PROFILE, CustomerProfile())
        )
        
        logger.info(f"generated_profile: {generated_profile}")
        return {
            SubProfileStateField.SUGGESTION_PROFILE: generated_profile,
            SubProfileStateField.NOTIFY_CONTENT: f"您好，我为您的客户生成了新的Profile，需要您的确认：\n{generated_profile.model_dump_json()}",
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"生成客户profile成功：\n{generated_profile.model_dump_json()}",
                task_result_explain=f"生成客户profile成功",
                task_result_code=0
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_SUGGEST.value,
                execute_result_code=0,
                execute_result_msg="生成客户profile成功，等待人工确认",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"生成客户profile失败: {e}")
        return {
            SubProfileStateField.SUGGESTION_PROFILE: CustomerProfile(),
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"很抱歉，生成客户profile时，发生了错误",
                task_result_explain=f"生成客户profile失败",
                task_result_code=1
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_SUGGEST.value,
                execute_result_code=1,
                execute_result_msg="生成客户profile失败",
                execute_exceptions=[str(e)]
            )]
        }

def notify_human_feedback(state: SubProfileState, config: RunnableConfig):
    """发送人工确认通知"""

    logger.info("=== 发送人工确认通知 ===")
    # 注意：必须 return，否则 node_result 丢失；且 reducer 遇到空 list 会清空历史累积
    return _notify_human(state, config, NodeName.PROFILE_NOTIFY_FEEDBACK)

def notify_human_result(state: SubProfileState, config: RunnableConfig):
    """发送任务结果通知"""
    logger.info("=== 发送任务结果通知 ===")
    return _notify_human(state, config, NodeName.PROFILE_NOTIFY_RESULT)

def _notify_human(state: SubProfileState, config: RunnableConfig, node_name: NodeName):
    """发送通知（经 NotifyPort，离线态走 mock，不发起网络请求）"""

    user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    content = state.get(SubProfileStateField.NOTIFY_CONTENT, "")

    if not user_id or not content :
        return {
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=node_name.value,
                execute_result_code=1,
                execute_result_msg=f"缺少参数，无法发送通知",
                execute_exceptions=[]
            )]
        }

    try:
        result = get_notify_port().notify(user_id=user_id, content=content)
        logger.info(f"用户通知成功: {result}")

        return {
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=node_name.value,
                execute_result_code=0 if result else 1,
                execute_result_msg="通知用户成功" if result else "通知用户失败",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"用户通知失败: {e}")
        return {
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=node_name.value,
                execute_result_code=1,
                execute_result_msg="通知用户失败",
                execute_exceptions=[str(e)]
            )]
        }

def human_feedback(state: SubProfileState, config: RunnableConfig) -> Command[Literal[NodeName.PROFILE_SUGGEST.value, NodeName.PROFILE_UPDATE.value, END]]:
    """人工反馈节点 - 在此处中断等待反馈"""

    ## 期待的反馈结果格式：
    #    {
    #        confirmed: "ok" | "discard" | "recreate"
    #    }
    ## debug in studio：
    # {"confirmed": "ok"}
    # {"confirmed": "discard"}
    # {"confirmed": "recreate"}

    human_feedback = interrupt({
        "description": "这是帮您生成的客户Profile，您可以确认、放弃、重新生成。",
        "old_profile": state.get(SubProfileStateField.CUSTOMER_PROFILE, {}),
        "new_profile": state.get(SubProfileStateField.SUGGESTION_PROFILE, {})
    })

    logger.info(f"用户反馈: {human_feedback}")
    logger.info(f"用户反馈类型: {type(human_feedback)}")
    logger.info(f"用户反馈内容: {json.dumps(human_feedback, ensure_ascii=False)}")

    # 解析JSON
    try:
        feedback_dict = human_feedback #json.loads(human_feedback)
        confirmed = feedback_dict.get(HumanFeedback.FIELD_NAME, "")
        logger.info(f"用户反馈: {confirmed}")
    except (json.JSONDecodeError, AttributeError) as e:
        # 如果解析失败，记录错误并结束
        logger.error(f"解析human_feedback失败: {e}")
        execute_result = {
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"解析human_feedback失败",
                task_result_explain=f"解析human_feedback失败",
                task_result_code=1
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_FEEDBACK.value,
                execute_result_code=1,
                execute_result_msg=f"解析human_feedback失败",
                execute_exceptions=[str(e)]
            )]
        }
        return Command(goto=END, update=execute_result)

    if confirmed == HumanFeedback.OK:
        logger.info("用户已确认结果符合要求")
        execute_result = {
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_FEEDBACK.value,
                execute_result_code=0,
                execute_result_msg=f"用户已确认结果，生产的Profile符合要求",
                execute_exceptions=[]
            )]
        }
        return Command(goto=NodeName.PROFILE_UPDATE.value, update=execute_result)
    
    if confirmed == HumanFeedback.DISCARD:
        logger.info("用户放弃结果")
        execute_result = {
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"用户放弃结果，生产的Profile不符合要求",
                task_result_explain=f"用户放弃结果，生产的Profile不符合要求",
                task_result_code=1
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_FEEDBACK.value,
                execute_result_code=0,
                execute_result_msg=f"用户放弃结果，生产的Profile不符合要求",
                execute_exceptions=[]
            )]
        }
        return Command(goto=END, update=execute_result)
    
    if confirmed == HumanFeedback.RECREATE:
        logger.info("用户需要重新生成")
        execute_result = {
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"用户需要重新生成Profile",
                task_result_explain=f"用户需要重新生成Profile",
                task_result_code=1
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_FEEDBACK.value,
                execute_result_code=0,
                execute_result_msg=f"用户需要重新生成Profile",
                execute_exceptions=[]
            )]
        }
        return Command(goto=NodeName.PROFILE_SUGGEST.value, update=execute_result)
    
    ## 其他异常情况，系统自动结束
    logger.info("指令异常，系统自动结束")
    execute_result = {
        SubProfileStateField.TASK_RESULT: TaskResult(
            task_result=f"指令异常，系统自动结束",
            task_result_explain=f"指令异常，系统自动结束",
            task_result_code=1
        ),
        SubProfileStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.PROFILE_FEEDBACK.value,
            execute_result_code=0,
            execute_result_msg=f"指令异常，系统自动结束",
            execute_exceptions=[]
        )]
    }
    return Command(goto=END, update=execute_result)



def update_customer_profile(state: SubProfileState, config: RunnableConfig):
    """更新客户profile节点"""
    
    logger.info("=== 更新客户profile ===")

    follow_user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    external_id = config[ConfigurableField.configurable][ConfigurableField.external_id]
    task_result = state.get(SubProfileStateField.TASK_RESULT, TaskResult())
    task_result_code = task_result.task_result_code

    if not external_id or not follow_user_id or task_result_code != 0:
        return {
            SubProfileStateField.NOTIFY_CONTENT: f"缺少参数或任务结果不成功，无法更新客户profile",
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"缺少参数或任务结果不成功，无法更新客户profile",
                task_result_explain=f"缺少参数或任务结果不成功，无法更新客户profile",
                task_result_code=1
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_UPDATE.value,
                execute_result_code=task_result_code,
                execute_result_msg=f"缺少参数或任务结果不成功，无法更新客户profile",
                execute_exceptions=[]
            )]
        }

 
    # 直接调用工具函数，传递配置
    try:
        profile = state.get(SubProfileStateField.SUGGESTION_PROFILE, CustomerProfile())
        update_customer_profile_tool(external_id = external_id, follow_user_id = follow_user_id, profile = profile)
        logger.info(f"客户profile更新成功")
        
        return {
            SubProfileStateField.NOTIFY_CONTENT: f"更新客户profile成功：\n{profile.model_dump_json()}",
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"更新客户profile成功：\n{profile.model_dump_json()}",
                task_result_explain=f"更新客户profile成功",
                task_result_code=0
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_UPDATE.value,
                execute_result_code=0,
                execute_result_msg=f"更新客户profile成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"更新客户profile失败: {e}")
        return {
            SubProfileStateField.NOTIFY_CONTENT: f"更新客户profile失败：\n{str(e)}",
            SubProfileStateField.TASK_RESULT: TaskResult(
                task_result=f"更新客户profile失败：\n{str(e)}",
                task_result_explain=f"更新客户profile失败",
                task_result_code=1
            ),
            SubProfileStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.PROFILE_UPDATE.value,
                execute_result_code=1,
                execute_result_msg=f"更新客户profile失败",
                execute_exceptions=[str(e)]
            )] 
        }



