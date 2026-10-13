"""
库存管理子图的状态定义。

字段口径
--------
* 输入要三样：查询原文（`task_input`）+ 物料主数据（把口语说法对到编码）
  + 库存快照（多仓明细在 `stock_items` 里，子图自己按物料筛）。
* 输出 `inventory_query` 是 `InventoryReport`（分仓明细 + 预警 + 调拨建议）。

只读链路：库存子图不写任何东西，连查两次必须逐字段相等 ——
这是「只读计算」与「有副作用的执行」之间的边界，测试会守住它。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmConfig, ScmStateField
from models.scm_models import (
    InventoryReport,
    MaterialInfo,
    NodeResult,
    StockItem,
    TaskResult,
    WarehouseInfo,
)


class SubInventoryStateField(str, Enum):
    # ---- 输入 ----
    TASK_INPUT  = ScmStateField.TASK_INPUT.value
    MATERIALS   = ScmStateField.MATERIALS.value
    WAREHOUSES  = ScmStateField.WAREHOUSES.value
    STOCK_ITEMS = ScmStateField.STOCK_ITEMS.value
    # ---- 输出 ----
    INVENTORY_QUERY = ScmStateField.INVENTORY_QUERY.value
    TASK_RESULT     = ScmStateField.TASK_RESULT.value
    NODE_RESULT     = ScmStateField.NODE_RESULT.value


class SubInventoryInputState(TypedDict):
    task_input:  str
    materials:   List[MaterialInfo]
    warehouses:  List[WarehouseInfo]
    stock_items: List[StockItem]


class SubInventoryOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    inventory_query: InventoryReport


class SubInventoryState(SubInventoryInputState, SubInventoryOutputState):
    """完整的子图状态（输入 + 输出）。"""
    pass


__all__ = [
    "SubInventoryStateField",
    "SubInventoryInputState",
    "SubInventoryOutputState",
    "SubInventoryState",
    "ScmConfig",
]
