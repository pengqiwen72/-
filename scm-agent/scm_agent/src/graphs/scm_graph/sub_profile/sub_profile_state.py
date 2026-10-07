import operator
from typing import TypedDict, Annotated, List
from graphs.scm_graph.scm_state import ScmStateField
from models.scm_models import ChatHistory, KFChatHistory, OrderHistory, CustomerProfile, CustomerTags, TaskResult, NodeResult
from enum import Enum


class SubProfileStateField(str, Enum):
    """子图状态字段名称枚举"""
    
    ## 输入字段
    CHAT_HISTORY = ScmStateField.CHAT_HISTORY.value
    KF_CHAT_HISTORY = ScmStateField.KF_CHAT_HISTORY.value
    ORDER_HISTORY = ScmStateField.ORDER_HISTORY.value
    CUSTOMER_TAGS = ScmStateField.CUSTOMER_TAGS.value
    CUSTOMER_PROFILE = ScmStateField.CUSTOMER_PROFILE.value
    
    ## 中间输出字段
    NOTIFY_CONTENT = ScmStateField.NOTIFY_CONTENT.value
    SUGGESTION_PROFILE = ScmStateField.SUGGESTION_PROFILE.value

    ## 输出字段
    TASK_RESULT = ScmStateField.TASK_RESULT.value
    NODE_RESULT = ScmStateField.NODE_RESULT.value
class SubProfileInputState(TypedDict):
    chat_history:       ChatHistory
    kf_chat_history:    KFChatHistory
    order_history:      OrderHistory
    customer_tags:      CustomerTags
    customer_profile:   CustomerProfile

class SubProfileIntermediateOutputState(TypedDict):
    notify_content:     str
    suggestion_profile: CustomerProfile


class SubProfileOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]

# 使用多重继承，自动合并所有字段
class SubProfileState(SubProfileInputState, SubProfileIntermediateOutputState, SubProfileOutputState):
    """
    完整的子图状态，包含输入和输出字段
    继承自 SubProfileInputState, SubProfileIntermediateOutputState 和 SubProfileOutputState
    """
    pass