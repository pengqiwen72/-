"""
物料与制度问答（RAG）子图的状态定义。

字段口径
--------
* 输入只要一样：问题（`task_input`）。菜单 id 直达时 task_input 是意图名本身，
  问题为空 —— 本子图的入口是「一句话提问」，没有别的输入块可回退，
  所以空问题会走「请补充问题」的澄清分支，而不是硬编一个答案。
* 输出 `material_qa` 是 pydantic 模型（`models.MaterialQA`），**不是** RAG 链路里的
  `rag.types.RagAnswer` dataclass。后者只在进程内流转，写进状态后无法跨检查点
  反序列化。跨了状态边界的数据必须是可序列化的模型，这条边界划在这里。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmConfig, ScmStateField
from models.scm_models import MaterialQA, NodeResult, TaskResult


class SubMaterialQAStateField(str, Enum):
    # ---- 输入 ----
    TASK_INPUT   = ScmStateField.TASK_INPUT.value
    # ---- 输出 ----
    MATERIAL_QA  = ScmStateField.MATERIAL_QA.value
    TASK_RESULT  = ScmStateField.TASK_RESULT.value
    NODE_RESULT  = ScmStateField.NODE_RESULT.value


class SubMaterialQAInputState(TypedDict):
    task_input: str


class SubMaterialQAOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃、
    #    不会回写到主图状态。子图的 output_schema 是一道**过滤器**，
    #    不是「顺带声明一下有什么」。
    material_qa: MaterialQA


class SubMaterialQAState(SubMaterialQAInputState, SubMaterialQAOutputState):
    """完整的子图状态（输入 + 输出）。"""
    pass


__all__ = [
    "SubMaterialQAStateField",
    "SubMaterialQAInputState",
    "SubMaterialQAOutputState",
    "SubMaterialQAState",
    "ScmConfig",
]
