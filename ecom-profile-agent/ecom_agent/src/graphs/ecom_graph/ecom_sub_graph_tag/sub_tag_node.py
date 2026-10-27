from typing_extensions import Literal
from langgraph.types import interrupt, Command
from langgraph.graph import END
from langchain_core.runnables import RunnableConfig
from tools.wechat_tool import WxWorkAPI
from models.ecom_models import TagSuggestion, TaskResult, NodeResult, TagSetting, CustomerTags, ChatHistory, KFChatHistory, OrderHistory
from graphs.ecom_graph.ecom_state import ConfigurableField
from graphs.ecom_graph.ecom_sub_graph_tag.sub_tag_state import SubTagState, SubTagStateField
from utils.agent_logger import get_logger
from llm.llm_suggest_tag import llm_tag_suggest
from tools.store_tool import update_customer_tags as update_customer_tags_tool

import json
from enum import Enum
from datetime import datetime

logger = get_logger("sub_tag_node")


class HumanFeedback(str, Enum):
    OK          = "ok"
    DISCARD     = "discard"
    RECREATE    = "recreate"
    FIELD_NAME  = "confirmed"

class NodeName(str, Enum):
    WELCOME_MESSAGE = "tag_welcome_node"
    GENERATE_TAG    = "tag_generate_node"
    HUMAN_FEEDBACK  = "tag_human_feedback_node"
    UPDATE_TAG      = "tag_update_node"
    NOTIFY_FEEDBACK = "tag_notify_feedback_node"
    NOTIFY_RESULT   = "tag_notify_result_node"

def welcome_message_node(state: SubTagState, config: RunnableConfig):
    """欢迎消息节点"""
    
    logger.info("=== 欢迎消息 ===")
    
    return {
        SubTagStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在为您生成会员标签建议，请稍等。",
            execute_exceptions=[]
        )]
    }

def generate_customer_tag(state: SubTagState, config: RunnableConfig):
    """生成会员标签建议节点"""
    
    logger.info("=== 生成会员标签建议 ===")

    try:
        generated_tag_suggestion = llm_tag_suggest(
            tag_setting = state.get(SubTagStateField.TAG_SETTING, TagSetting()),
            customer_tags = state.get(SubTagStateField.CUSTOMER_TAGS, CustomerTags()),
            chat_history = state.get(SubTagStateField.CHAT_HISTORY, ChatHistory()),
            kf_chat_history = state.get(SubTagStateField.KF_CHAT_HISTORY, KFChatHistory()),
            order_history = state.get(SubTagStateField.ORDER_HISTORY, OrderHistory()),
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return {
            SubTagStateField.SUGGESTION_TAG: generated_tag_suggestion,
            SubTagStateField.NOTIFY_CONTENT: f"您好，我为您的会员生成了新的标签建议，需要您的确认：\n{generated_tag_suggestion.model_dump_json()}",
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"生成会员标签建议成功：\n{generated_tag_suggestion.model_dump_json()}",
                task_result_explain=f"生成会员标签建议成功",
                task_result_code=0
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_TAG.value,
                execute_result_code=0,
                execute_result_msg="生成会员标签建议成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"生成会员标签建议失败: {e}")
        return {
            SubTagStateField.SUGGESTION_TAG: TagSuggestion(),
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"生成会员标签建议失败",
                task_result_explain=f"生成会员标签建议失败",
                task_result_code=1
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.GENERATE_TAG.value,
                execute_result_code=1,
                execute_result_msg=f"生成会员标签建议失败: {e}",
                execute_exceptions=[str(e)]
            )]
        }

def notify_human_feedback(state: SubTagState, config: RunnableConfig):
    """发送人工确认通知"""
    
    logger.info("=== 发送人工确认通知 ===")
    _notify_human(state, config, NodeName.NOTIFY_FEEDBACK)

def notify_human_result(state: SubTagState, config: RunnableConfig):
    """发送任务结果通知"""
    logger.info("=== 发送任务结果通知 ===")
    _notify_human(state, config, NodeName.NOTIFY_RESULT)

def _notify_human(state: SubTagState, config: RunnableConfig, node_name: NodeName):
    """发送通知"""
    
    wxwork_api = WxWorkAPI()

    user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    content = state.get(SubTagStateField.NOTIFY_CONTENT, "")

    if not user_id or not content :
        return {
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=node_name.value,
                execute_result_code=1,
                execute_result_msg=f"缺少参数，无法发送通知",
                execute_exceptions=[]
            )]
        }
    
    # 调用企业微信API，发送通知
    try:
        result = wxwork_api.notify_user(user_id = user_id, content = content)
        logger.info(f"用户通知成功: {result}")
        
        return {
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=node_name.value,
                execute_result_code=0,
                execute_result_msg="通知用户成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"用户通知失败: {e}")
        return {
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=node_name.value,
                execute_result_code=1,
                execute_result_msg="通知用户失败",
                execute_exceptions=[str(e)]
            )]
        }
    
def human_feedback(state: SubTagState, config: RunnableConfig) -> Command[Literal[NodeName.GENERATE_TAG.value, NodeName.UPDATE_TAG.value, END]]:
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
        "description": "这是帮您生成的会员标签建议，您可以确认、放弃、重新生成。",
        "old_tags": state.get(SubTagStateField.CUSTOMER_TAGS, {}),
        "new_tags": state.get(SubTagStateField.SUGGESTION_TAG, {})
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
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"解析human_feedback失败",
                task_result_explain=f"解析human_feedback失败",
                task_result_code=1
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.HUMAN_FEEDBACK.value,
                execute_result_code=1,
                execute_result_msg=f"解析human_feedback失败",
                execute_exceptions=[str(e)]
            )]  
        }
        return Command(goto=END, update=execute_result)

    if confirmed == HumanFeedback.OK:
        logger.info("用户已确认结果符合要求")
        execute_result = {
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.HUMAN_FEEDBACK.value,
                execute_result_code=0,
                execute_result_msg=f"用户已确认结果符合要求",
                execute_exceptions=[]
            )]
        }
        return Command(goto=NodeName.UPDATE_TAG.value, update=execute_result)
    
    if confirmed == HumanFeedback.DISCARD:
        logger.info("用户放弃结果")
        execute_result = {
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"用户放弃结果",
                task_result_explain=f"用户放弃结果",
                task_result_code=1
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.HUMAN_FEEDBACK.value,
                execute_result_code=0,
                execute_result_msg=f"用户放弃结果",
                execute_exceptions=[]
            )]
        }
        return Command(goto=END, update=execute_result)
    
    if confirmed == HumanFeedback.RECREATE:
        logger.info("用户需要重新生成")
        execute_result = {
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"用户需要重新生成",
                task_result_explain=f"用户需要重新生成",
                task_result_code=1
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.HUMAN_FEEDBACK.value,
                execute_result_code=0,
                execute_result_msg=f"用户需要重新生成",
                execute_exceptions=[]
            )]
        }
        return Command(goto=NodeName.GENERATE_TAG.value, update=execute_result)
    
    ## 其他异常情况，系统自动结束
    logger.info("指令异常，系统自动结束")
    execute_result = {
        SubTagStateField.TASK_RESULT: TaskResult(
            task_result=f"指令异常，系统自动结束",
            task_result_explain=f"指令异常，系统自动结束",
            task_result_code=1
        ),
        SubTagStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.HUMAN_FEEDBACK.value,
            execute_result_code=0,
            execute_result_msg=f"指令异常，系统自动结束",
            execute_exceptions=[]
        )]
    }
    return Command(goto=END, update=execute_result)

    


def update_customer_tag(state: SubTagState, config: RunnableConfig):
    """更新会员标签节点"""
    
    logger.info("=== 更新会员标签 ===")

    user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    external_id = config[ConfigurableField.configurable][ConfigurableField.external_id]
 
    # 直接调用工具函数，传递配置
    try:
        ## 暂时不更新企业微信的用户标签，方便学员运行调试
        ## 正式环境需要更新企业微信的用户标签（用户标签需要使用同一个自建应用创建/维护的标签才能被该应用更新）
        # wxwork_api = WxWorkAPI()
        
        tag_suggestion = state.get(SubTagStateField.SUGGESTION_TAG, TagSuggestion())
        tags_add = tag_suggestion.tag_ids_add
        tags_remove = tag_suggestion.tag_ids_remove

        tag_ids_add = [tag.tag_id for tag in tags_add if tag.tag_id]
        tag_ids_remove = [tag.tag_id for tag in tags_remove if tag.tag_id]

        if len(tag_ids_add) == 0 and len(tag_ids_remove) == 0:
            logger.info("没有需要更新的标签")
            return {
                SubTagStateField.NOTIFY_CONTENT: f"您好，没有需要更新的标签，任务结束", 
                SubTagStateField.TASK_RESULT: TaskResult(
                    task_result=f"没有需要更新的标签",
                    task_result_explain=f"没有需要更新的标签",
                    task_result_code=0
                ),
                SubTagStateField.NODE_RESULT: [NodeResult(
                    execute_node_name=NodeName.UPDATE_TAG.value,
                    execute_result_code=0,
                    execute_result_msg="没有需要更新的标签，任务结束",
                    execute_exceptions=[]
                )]
            }

        # 调用企业微信API，更新会员标签（暂时不更新企业微信的用户标签，方便学员运行调试）
        # 更改成调用sagt自有的长期记忆模式存储用户标签
        # wxwork_api.update_customer_tag(user_id = user_id, external_id = external_id, tag_ids_add = tag_ids_add, tag_ids_remove = tag_ids_remove)
        
        # 调用sagt自有的长期记忆模式存储用户标签
        # def update_customer_tags(external_id: str, follow_user_id: str, tag_ids_add: List[str], tag_ids_remove: List[str])
        update_customer_tags_tool(external_id = external_id, follow_user_id = user_id, tag_ids_add = tag_ids_add, tag_ids_remove = tag_ids_remove)

        logger.info(f"会员标签更新成功")
        
        return {
            SubTagStateField.NOTIFY_CONTENT: f"您好，我已为您更新会员标签，更新结果如下：\n{tag_suggestion.model_dump_json()}",
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"更新会员标签成功：\n{tag_suggestion.model_dump_json()}",
                task_result_explain=f"更新会员标签成功",
                task_result_code=0
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.UPDATE_TAG.value,
                execute_result_code=0,
                execute_result_msg="更新会员标签成功",
                execute_exceptions=[]
            )]  
        }
    except Exception as e:
        logger.error(f"更新会员标签失败: {e}")
        return {
            SubTagStateField.NOTIFY_CONTENT: f"很抱歉，更新会员标签时，发生了错误：\n{str(e)}",
            SubTagStateField.TASK_RESULT: TaskResult(
                task_result=f"更新会员标签失败",
                task_result_explain=f"更新会员标签失败",
                task_result_code=1
            ),
            SubTagStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.UPDATE_TAG.value,
                execute_result_code=1,
                execute_result_msg=f"更新会员标签失败: {e}",
                execute_exceptions=[str(e)]
            )]
        }