import operator
from typing import TypedDict, Annotated, List
from graphs.cs_graph.cs_state import CsStateField
from models.cs_models import ChatHistory, KFChatHistory, OrderHistory, CustomerProfile, TagSuggestion, TagSetting, CustomerTags
from models.cs_models import TaskResult, NodeResult
from enum import Enum

class SubTagStateField(str, Enum):
    """子图状态字段名称枚举"""
    # 输入字段
    TAG_SETTING = CsStateField.TAG_SETTING.value
    CHAT_HISTORY = CsStateField.CHAT_HISTORY.value
    KF_CHAT_HISTORY = CsStateField.KF_CHAT_HISTORY.value
    ORDER_HISTORY = CsStateField.ORDER_HISTORY.value
    CUSTOMER_TAGS = CsStateField.CUSTOMER_TAGS.value
    CUSTOMER_PROFILE = CsStateField.CUSTOMER_PROFILE.value
    # 中间输出字段
    NOTIFY_CONTENT = CsStateField.NOTIFY_CONTENT.value
    SUGGESTION_TAG = CsStateField.SUGGESTION_TAG.value
    # 输出字段
    TASK_RESULT = CsStateField.TASK_RESULT.value
    NODE_RESULT = CsStateField.NODE_RESULT.value

class SubTagInputState(TypedDict):
    tag_setting:        TagSetting
    chat_history:       ChatHistory
    kf_chat_history:    KFChatHistory
    order_history:      OrderHistory
    customer_tags:      CustomerTags
    customer_profile:   CustomerProfile

class SubTagIntermediateState(TypedDict):
    notify_content:     str
    suggestion_tag:     TagSuggestion

class SubTagOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]

# 使用多重继承，自动合并所有字段
class SubTagState(SubTagInputState, SubTagIntermediateState, SubTagOutputState):
    """
    完整的子图状态，包含输入和输出字段
    继承自 SubTagInputState, SubTagIntermediateState 和 SubTagOutputState
    """
    pass