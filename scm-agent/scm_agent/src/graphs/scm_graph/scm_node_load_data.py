from langchain_core.runnables import RunnableConfig
from graphs.scm_graph.scm_state import ScmState, ScmStateField
from tools.store_tool import get_tag_setting, get_customer_info, get_chat_history, get_kf_history, get_order_history, get_customer_profile, get_customer_tags, get_employee_info
from graphs.scm_graph.scm_state import ConfigurableField
from models.scm_models import TagSetting, CustomerInfo, CustomerProfile, OrderHistory, KFChatHistory, ChatHistory, CustomerTags, NodeResult, EmployeeInfo
from utils.agent_logger import get_logger
from enum import Enum

logger = get_logger("scm_node_load_data")

class NodeName(str, Enum):
    LOAD_WELCOME_MESSAGE    = "load_welcome_message_node"
    LOAD_EMPLOYEE_INFO      = "load_employee_info_node"
    LOAD_TAG_SETTING        = "load_tag_setting_node"
    LOAD_CUSTOMER_INFO      = "load_customer_info_node"
    LOAD_CHAT_HISTORY       = "load_chat_history_node"
    LOAD_KF_CHAT_HISTORY    = "load_kf_chat_history_node"
    LOAD_ORDER_HISTORY      = "load_order_history_node"

def load_welcome_message_node(state: ScmState, config: RunnableConfig):
    """欢迎信息节点"""
    
    logger.info("=== 欢迎信息 ===")
    
    return {
        ScmStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.LOAD_WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在为您加载数据，请稍等。",
            execute_exceptions=[]
        )]
    }

def load_employee_info_node(state: ScmState, config: RunnableConfig):
    """加载员工信息节点"""
    
    logger.info("=== 加载员工信息 ===")

    user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]

    logger.info(f"user_id: {user_id}")

    try:
        employee_info: EmployeeInfo = get_employee_info(user_id = user_id)
        logger.info(f"员工信息: {employee_info}")
        
        return {
            ScmStateField.EMPLOYEE_INFO: employee_info,
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_EMPLOYEE_INFO.value,
                execute_result_code=0,
                execute_result_msg="完成员工信息加载",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"加载员工信息失败: {e}")
        return {
            ScmStateField.EMPLOYEE_INFO: EmployeeInfo(),
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_EMPLOYEE_INFO.value,
                execute_result_code=1,
                execute_result_msg="加载员工信息失败",
                execute_exceptions=[str(e)]
            )]
        }

def load_tag_setting_node(state: ScmState, config: RunnableConfig):
    """加载标签设置节点"""
    
    logger.info("=== 加载标签设置 ===")
    
    # 直接调用工具函数，传递配置
    try:
        tag_setting: TagSetting = get_tag_setting()
        logger.info(f"标签设置: {tag_setting}")
        
        return {
            ScmStateField.TAG_SETTING: tag_setting,
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_TAG_SETTING.value,
                execute_result_code=0,
                execute_result_msg="完成标签设置加载",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"加载标签设置失败: {e}")
        return {
            ScmStateField.TAG_SETTING: TagSetting(),
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_TAG_SETTING.value,
                execute_result_code=1,
                execute_result_msg="加载标签设置失败",
                execute_exceptions=[str(e)]
            )]
        }


def load_customer_info_node(state: ScmState, config: RunnableConfig):
    """加载客户信息节点"""
    
    logger.info("=== 加载客户信息 ===")
    
    follow_user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    external_id    = config[ConfigurableField.configurable][ConfigurableField.external_id]
    
    logger.info(f"follow_user_id: {follow_user_id}")
    logger.info(f"external_id: {external_id}")
    
    # 直接调用工具函数，传递配置
    try:
        customer_info: CustomerInfo       = get_customer_info(external_id = external_id, follow_user_id = follow_user_id)
        customer_profile: CustomerProfile = get_customer_profile(external_id = external_id, follow_user_id = follow_user_id)
        customer_tags: CustomerTags       = get_customer_tags(external_id = external_id, follow_user_id = follow_user_id)

        logger.debug(f"客户信息: {customer_info}")
        logger.debug(f"客户画像: {customer_profile}")
        logger.debug(f"客户标签: {customer_tags}")
        
        return {
            ScmStateField.CUSTOMER_INFO: customer_info,
            ScmStateField.CUSTOMER_PROFILE: customer_profile,
            ScmStateField.CUSTOMER_TAGS: customer_tags,
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_CUSTOMER_INFO.value,
                execute_result_code=0,
                execute_result_msg="完成客户信息加载",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"加载客户信息失败: {e}")
        return {
            ScmStateField.CUSTOMER_INFO: CustomerInfo(),
            ScmStateField.CUSTOMER_PROFILE: CustomerProfile(),
            ScmStateField.CUSTOMER_TAGS: [],
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_CUSTOMER_INFO.value,
                execute_result_code=1,
                execute_result_msg="加载客户信息失败",
                execute_exceptions=[str(e)]
            )]
        }


def load_chat_history_node(state: ScmState, config: RunnableConfig):
    """加载聊天消息节点"""
    
    logger.info("=== 加载聊天消息 ===")

    follow_user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    external_id    = config[ConfigurableField.configurable][ConfigurableField.external_id]
    
    logger.info(f"follow_user_id: {follow_user_id}")
    logger.info(f"external_id: {external_id}")
    

    # 直接调用工具函数，传递配置
    try:
        chat_history = get_chat_history(external_id = external_id,follow_user_id = follow_user_id)
        logger.debug(f"聊天消息: {chat_history}")
        
        return {
            ScmStateField.CHAT_HISTORY: chat_history,
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_CHAT_HISTORY.value,
                execute_result_code=0,
                execute_result_msg="完成聊天消息加载",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"加载聊天消息失败: {e}")
        
        return {
            ScmStateField.CHAT_HISTORY: ChatHistory(),
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_CHAT_HISTORY.value,
                execute_result_code=1,
                execute_result_msg="加载聊天消息失败",
                execute_exceptions=[str(e)]
            )]
        }


def load_kf_chat_history_node(state: ScmState, config: RunnableConfig):
    """加载微信客服信息节点"""
    
    logger.info("=== 加载微信客服信息 ===")

    external_id = config[ConfigurableField.configurable][ConfigurableField.external_id]
    
    logger.info(f"external_id: {external_id}")
    

    # 直接调用工具函数，传递配置
    try:
        kf_chat_history = get_kf_history(external_id = external_id)
        logger.debug(f"微信客服信息: {kf_chat_history}")
        
        return {
            ScmStateField.KF_CHAT_HISTORY: kf_chat_history,
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_KF_CHAT_HISTORY.value,
                execute_result_code=0,
                execute_result_msg="完成微信客服信息加载",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"加载微信客服信息失败: {e}")
        return {
            ScmStateField.KF_CHAT_HISTORY: KFChatHistory(),
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_KF_CHAT_HISTORY.value,
                execute_result_code=1,
                execute_result_msg="加载微信客服信息失败",
                execute_exceptions=[str(e)]
            )]
        }



def load_order_history_node(state: ScmState, config: RunnableConfig):
    """加载订单信息节点"""
    
    logger.info("=== 加载订单信息 ===")

    follow_user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]
    external_id    = config[ConfigurableField.configurable][ConfigurableField.external_id]
    
    logger.info(f"follow_user_id: {follow_user_id}")
    logger.info(f"external_id: {external_id}")
    
    customer_info: CustomerInfo = get_customer_info(external_id = external_id, follow_user_id = follow_user_id)

    union_id = customer_info.union_id

    # 直接调用工具函数，传递配置
    try:

        if not union_id:
            return {
                ScmStateField.ORDER_HISTORY: OrderHistory(),
                ScmStateField.NODE_RESULT: [NodeResult(
                    execute_node_name=NodeName.LOAD_ORDER_HISTORY.value,
                    execute_result_code=1,
                    execute_result_msg="没有获取到该客户的union_id",
                    execute_exceptions=[f"客户信息中没有union_id"]
                )]
            }

        order_history: OrderHistory = get_order_history(union_id = union_id)
        logger.info(f"订单信息: {order_history}")
        
        return {
            ScmStateField.ORDER_HISTORY: order_history,
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_ORDER_HISTORY.value,
                execute_result_code=0,
                execute_result_msg="完成订单信息加载",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"加载订单信息失败: {e}")
        return {
            ScmStateField.ORDER_HISTORY: OrderHistory(),
            ScmStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.LOAD_ORDER_HISTORY.value,
                execute_result_code=1,
                execute_result_msg="加载订单信息失败",
                execute_exceptions=[str(e)]
            )]
        }