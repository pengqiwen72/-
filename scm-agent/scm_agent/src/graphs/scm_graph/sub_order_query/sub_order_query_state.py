"""订单查询子图的 State 定义。"""

import operator
from enum import Enum
from typing_extensions import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmStateField
from models.scm_models import (
    ChatHistory,
    CustomerInfo,
    NodeResult,
    OrderHistory,
    OrderQueryResult,
    TaskResult,
)


class SubOrderQueryStateField(str, Enum):
    ''' 订单查询子图的字段名 '''

    TASK_INPUT     = ScmStateField.TASK_INPUT.value
    CHAT_HISTORY   = ScmStateField.CHAT_HISTORY.value
    ORDER_HISTORY  = ScmStateField.ORDER_HISTORY.value
    CUSTOMER_INFO  = ScmStateField.CUSTOMER_INFO.value
    ORDER_QUERY    = ScmStateField.ORDER_QUERY.value
    TASK_RESULT    = ScmStateField.TASK_RESULT.value
    NODE_RESULT    = ScmStateField.NODE_RESULT.value


class SubOrderQueryInputState(TypedDict):
    task_input:    str
    chat_history:  ChatHistory
    order_history: OrderHistory
    customer_info: CustomerInfo


class SubOrderQueryOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    #    子图的 output_schema 是一道**过滤器**，不是「顺带声明一下有什么」。
    order_query: OrderQueryResult


class SubOrderQueryState(SubOrderQueryInputState, SubOrderQueryOutputState):
    ''' 订单查询子图 State '''
    pass
