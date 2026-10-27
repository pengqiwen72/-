import operator
from typing import TypedDict, Annotated, List
from graphs.ecom_graph.ecom_state import EcomStateField
from models.ecom_models import ChatHistory, OrderHistory, CustomerInfo, CustomerProfile, ReplySuggestion, CustomerTags, TaskResult, EmployeeInfo, NodeResult
from enum import Enum

class SubChatSuggestionStateField(str, Enum):
    """子图状态字段名称枚举"""
    
    ## 输入字段
    EMPLOYEE_INFO    = EcomStateField.EMPLOYEE_INFO.value
    CHAT_HISTORY     = EcomStateField.CHAT_HISTORY.value
    ORDER_HISTORY    = EcomStateField.ORDER_HISTORY.value
    CUSTOMER_INFO    = EcomStateField.CUSTOMER_INFO.value
    CUSTOMER_TAGS    = EcomStateField.CUSTOMER_TAGS.value
    CUSTOMER_PROFILE = EcomStateField.CUSTOMER_PROFILE.value

    ## 中间输出字段
    SUGGESTION_CHAT     = EcomStateField.SUGGESTION_CHAT.value

    ## 输出字段
    TASK_RESULT         = EcomStateField.TASK_RESULT.value
    NODE_RESULT         = EcomStateField.NODE_RESULT.value

class SubChatSuggestionInputState(TypedDict):
    """子图的输入状态"""
    employee_info:      EmployeeInfo
    chat_history:       ChatHistory
    order_history:      OrderHistory
    customer_info:      CustomerInfo
    customer_tags:      CustomerTags
    customer_profile:   CustomerProfile

class SubChatSuggestionIntermediateOutputState(TypedDict):
    """子图的中间输出状态"""
    suggestion_chat:    ReplySuggestion

class SubChatSuggestionOutputState(TypedDict):
    """子图的输出状态"""
    task_result:        TaskResult  # 任务结果
    node_result:        Annotated[List[NodeResult], operator.add]  # 节点执行结果

# 使用多重继承，自动合并所有字段
class SubChatSuggestionState(SubChatSuggestionInputState, SubChatSuggestionIntermediateOutputState, SubChatSuggestionOutputState):
    """
    完整的子图状态，包含输入和输出字段
    继承自 SubChatSuggestionInputState, SubChatSuggestionIntermediateOutputState 和 SubChatSuggestionOutputState
    """
    pass