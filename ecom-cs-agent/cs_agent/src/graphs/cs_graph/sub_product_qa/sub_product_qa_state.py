"""
商品咨询（RAG）子图的状态定义。

字段口径
--------
* 输入只要两样：客户的问题（`task_input`）和会话记录（`chat_history`）。
  意图枚举直达时 `task_input` 是意图名，问题得从会话里取——见 node 里的
  `_resolve_question`。
* 输出 `product_qa` 是 pydantic 模型（`models.ProductQA`），**不是** RAG 链路里的
  `rag.types.RagAnswer` dataclass。后者只在进程内流转，写进状态后无法跨检查点
  反序列化。跨了状态边界的数据必须是可序列化的模型，这条边界划在这里。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict

from graphs.cs_graph.cs_state import CsConfig, CsStateField
from models.cs_models import ChatHistory, NodeResult, ProductQA, TaskResult


class SubProductQAStateField(str, Enum):
    # ---- 输入 ----
    TASK_INPUT   = CsStateField.TASK_INPUT.value
    CHAT_HISTORY = CsStateField.CHAT_HISTORY.value
    # ---- 输出 ----
    PRODUCT_QA   = CsStateField.PRODUCT_QA.value
    TASK_RESULT  = CsStateField.TASK_RESULT.value
    NODE_RESULT  = CsStateField.NODE_RESULT.value


class SubProductQAInputState(TypedDict):
    task_input: str
    chat_history: ChatHistory


class SubProductQAOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃、
    #    不会回写到主图状态。子图的 output_schema 是一道**过滤器**，
    #    不是「顺带声明一下有什么」。
    product_qa: ProductQA


class SubProductQAState(SubProductQAInputState, SubProductQAOutputState):
    """完整的子图状态（输入 + 输出）。"""
    pass


__all__ = [
    "SubProductQAStateField",
    "SubProductQAInputState",
    "SubProductQAOutputState",
    "SubProductQAState",
    "CsConfig",
]
