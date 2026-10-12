"""
供应商管理子图的状态定义。

字段口径
--------
* 输入要两样：查询原文（`task_input`）+ 物料主数据（定位到物料才能比价）
  + 供应商主数据（交期、准时率、不良率都在它上面）。
* 输出 `supplier_query` 是**两种报告之一**：
    - `SupplierComparison`：按综合得分降序的比价排名（定位到物料）；
    - `SupplierEvaluation`：单家供应商的履约评估（定位到供应商）。
  定位到哪个目标决定出哪种报告，两处共用同一个输出字段——webapp 与 CLI
  都只消费 `task_result` 的渲染文本，字段类型差异不外溢。
  报价明细不放进状态 —— 它是「查询用的事实」，结论（排名 / 评分）才是产物。

只读链路：比价与评估都不落库，同一个种子连查两次必须逐字段相等。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict, Union

from graphs.scm_graph.scm_state import ScmConfig, ScmStateField
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    SupplierComparison,
    SupplierEvaluation,
    SupplierInfo,
    TaskResult,
)


class SubSupplierStateField(str, Enum):
    # ---- 输入 ----
    TASK_INPUT = ScmStateField.TASK_INPUT.value
    MATERIALS  = ScmStateField.MATERIALS.value
    SUPPLIERS  = ScmStateField.SUPPLIERS.value
    # ---- 输出 ----
    SUPPLIER_QUERY = ScmStateField.SUPPLIER_QUERY.value
    TASK_RESULT    = ScmStateField.TASK_RESULT.value
    NODE_RESULT    = ScmStateField.NODE_RESULT.value


class SubSupplierInputState(TypedDict):
    task_input: str
    materials:  List[MaterialInfo]
    suppliers:  List[SupplierInfo]


class SubSupplierOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    supplier_query: Union[SupplierComparison, SupplierEvaluation]


class SubSupplierState(SubSupplierInputState, SubSupplierOutputState):
    """完整的子图状态（输入 + 输出）。"""
    pass


__all__ = [
    "SubSupplierStateField",
    "SubSupplierInputState",
    "SubSupplierOutputState",
    "SubSupplierState",
    "ScmConfig",
]
