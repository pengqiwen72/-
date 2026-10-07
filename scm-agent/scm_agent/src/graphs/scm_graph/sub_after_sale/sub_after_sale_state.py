"""售后处理子图的 State 定义。"""

import operator
from enum import Enum
from typing_extensions import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmStateField
from models.scm_models import (
    AfterSalePlan,
    ChatHistory,
    CustomerInfo,
    NodeResult,
    OrderHistory,
    TaskResult,
)


class SubAfterSaleStateField(str, Enum):
    ''' 售后处理子图的字段名 '''

    TASK_INPUT    = ScmStateField.TASK_INPUT.value
    CHAT_HISTORY  = ScmStateField.CHAT_HISTORY.value
    ORDER_HISTORY = ScmStateField.ORDER_HISTORY.value
    CUSTOMER_INFO = ScmStateField.CUSTOMER_INFO.value
    AFTER_SALE    = ScmStateField.AFTER_SALE.value
    TASK_RESULT   = ScmStateField.TASK_RESULT.value
    NODE_RESULT   = ScmStateField.NODE_RESULT.value


class SubAfterSaleInputState(TypedDict):
    task_input:    str
    chat_history:  ChatHistory
    order_history: OrderHistory
    customer_info: CustomerInfo


class SubAfterSaleOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    after_sale: AfterSalePlan


class SubAfterSaleState(SubAfterSaleInputState, SubAfterSaleOutputState):
    ''' 售后处理子图 State '''
    pass
