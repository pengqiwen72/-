"""
供应商管理子图的状态定义。

字段口径
--------
* 输入要两样：查询原文（`task_input`）+ 物料主数据（定位到物料才能比价）
  + 供应商主数据（交期、准时率、不良率都在它上面）。
* 输出 `supplier_query` 是 `SupplierComparison`（按综合得分降序的评分列表）。
  报价明细不放进状态 —— 它是「查询用的事实」，结论（评分排名）才是链路的产物。

只读链路：比价不落库，同一个种子连查两次必须逐字段相等。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmConfig, ScmStateField
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    SupplierComparison,
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
    supplier_query: SupplierComparison


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
