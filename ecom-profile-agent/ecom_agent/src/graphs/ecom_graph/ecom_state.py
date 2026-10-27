import operator
from typing_extensions import Annotated, List, TypedDict
from enum import Enum
from models.ecom_models import ScheduleSuggestion, TagSetting, EmployeeInfo, ChatHistory, KFChatHistory, OrderHistory, TaskResult, NodeResult
from models.ecom_models import CustomerInfo, TagSuggestion, ReplySuggestion, CustomerProfile, CustomerTags


def reducer_node_result(current: List[NodeResult], update: List[NodeResult]) -> List[NodeResult]:
    """ 自定义node_result的reducer，用于合并node_result """
    # 如果update为空，则置空node_result
    if not update: 
        return []

    #否则，追加
    return current + update

class InputState(TypedDict):
    '''
    主图的输入State
    '''
    task_input: str


class OutputState(TypedDict):
    '''
    主图的输出State
    '''
    task_result:        TaskResult
    node_result:        Annotated[List[NodeResult], reducer_node_result]

class IntermediateInputState(TypedDict):
    employee_info:      EmployeeInfo
    chat_history:       ChatHistory
    kf_chat_history:    KFChatHistory
    order_history:      OrderHistory
    tag_setting:        TagSetting
    customer_info:      CustomerInfo
    customer_tags:      CustomerTags
    customer_profile:   CustomerProfile

class IntermediateOutputState(TypedDict):
    suggestion_profile: CustomerProfile
    suggestion_tag:     TagSuggestion
    suggestion_chat:    ReplySuggestion
    suggestion_kf:      ReplySuggestion
    suggestion_schedule:ScheduleSuggestion
    notify_content:     str

class EcomState(InputState,IntermediateInputState,IntermediateOutputState,OutputState):
    '''
    主图的State，继承InputState,IntermediateInputState,IntermediateOutputState,OutputState
    '''
    pass

class EcomStateField(str, Enum):
    '''
    主图的State字段名称定义
    '''

    ## 任务输入
    TASK_INPUT          ="task_input"

    ## 信息Load
    EMPLOYEE_INFO       ="employee_info"
    CHAT_HISTORY        ="chat_history"
    KF_CHAT_HISTORY     ="kf_chat_history"
    ORDER_HISTORY       ="order_history"
    TAG_SETTING         ="tag_setting"
    CUSTOMER_INFO       ="customer_info"
    CUSTOMER_TAGS       ="customer_tags"
    CUSTOMER_PROFILE    ="customer_profile"

    ## Intermediate Output 中间输出
    SUGGESTION_PROFILE  ="suggestion_profile"
    SUGGESTION_TAG      ="suggestion_tag"
    SUGGESTION_CHAT     ="suggestion_chat"
    SUGGESTION_KF       ="suggestion_kf"
    SUGGESTION_SCHEDULE ="suggestion_schedule"
    NOTIFY_CONTENT      ="notify_content"

    ## 执行结果
    TASK_RESULT         ="task_result"
    NODE_RESULT         ="node_result"


class EcomConfig(TypedDict):
    '''
    主图的Config
    '''
    user_id:      str
    external_id:  str


class ConfigurableField(str, Enum):
    '''
    主图的配置字段名称定义
    '''
    configurable      ="configurable"
    user_id           ="user_id"
    external_id       ="external_id"
    
