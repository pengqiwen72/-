import operator
from typing import TypedDict, Annotated, List
from graphs.cs_graph.cs_state import CsStateField
from models.cs_models import KFChatHistory, CustomerInfo, ReplySuggestion, TaskResult, NodeResult
from enum import Enum


class SubKFChatSuggestionStateField(str, Enum):
    """子图状态字段名称枚举"""
    
    ## 输入字段
    KF_CHAT_HISTORY = CsStateField.KF_CHAT_HISTORY.value
    CUSTOMER_INFO = CsStateField.CUSTOMER_INFO.value

    ## 中间输出字段
    SUGGESTION_KF = CsStateField.SUGGESTION_KF.value

    ## 输出字段
    TASK_RESULT = CsStateField.TASK_RESULT.value
    NODE_RESULT = CsStateField.NODE_RESULT.value

class SubKFChatSuggestionInputState(TypedDict):
    """子图的输入状态"""
    customer_info:      CustomerInfo
    kf_chat_history:    KFChatHistory

class SubKFChatSuggestionIntermediateOutputState(TypedDict):
    """子图的中间输出状态"""
    suggestion_kf:      ReplySuggestion

class SubKFChatSuggestionOutputState(TypedDict):
    """子图的输出状态"""
    task_result:        TaskResult
    node_result:        Annotated[List[NodeResult], operator.add]
    
# 使用多重继承，自动合并所有字段
class SubKFChatSuggestionState(SubKFChatSuggestionInputState, SubKFChatSuggestionIntermediateOutputState, SubKFChatSuggestionOutputState):
    """
    完整的子图状态，包含输入和输出字段
    继承自 SubKFChatSuggestionInputState, SubKFChatSuggestionIntermediateOutputState 和 SubKFChatSuggestionOutputState
    """
    pass