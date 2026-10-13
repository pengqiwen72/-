"""
收货验收子图的状态定义。

字段口径
--------
* 输入要四样：登记原文（`task_input`）+ 候选物料/仓库（抽取时给模型做候选）
  + 在途/历史采购订单（按 po_no 认单，物料与到货仓从订单上取，不靠用户复述）。
* 输出 `goods_receipt` 是 `ReceiptPlan`：不良率、处置方式、复核状态都在里面。

字段名与菜单 id 对齐（`goods_receipt`）——「状态里哪块被填了」就等于
「哪个子图跑过了」。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmConfig, ScmStateField
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    OperatorInfo,
    PurchaseOrderInfo,
    ReceiptPlan,
    TaskResult,
    WarehouseInfo,
)


class SubGoodsReceiptStateField(str, Enum):
    # ---- 输入 ----
    TASK_INPUT      = ScmStateField.TASK_INPUT.value
    MATERIALS       = ScmStateField.MATERIALS.value
    WAREHOUSES      = ScmStateField.WAREHOUSES.value
    PURCHASE_ORDERS = ScmStateField.PURCHASE_ORDERS.value
    OPERATOR_INFO   = ScmStateField.OPERATOR_INFO.value
    # ---- 输出 ----
    GOODS_RECEIPT = ScmStateField.GOODS_RECEIPT.value
    TASK_RESULT   = ScmStateField.TASK_RESULT.value
    NODE_RESULT   = ScmStateField.NODE_RESULT.value


class SubGoodsReceiptInputState(TypedDict):
    task_input:      str
    materials:       List[MaterialInfo]
    warehouses:      List[WarehouseInfo]
    purchase_orders: List[PurchaseOrderInfo]
    operator_info:   OperatorInfo


class SubGoodsReceiptOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    goods_receipt: ReceiptPlan


class SubGoodsReceiptState(SubGoodsReceiptInputState, SubGoodsReceiptOutputState):
    """完整的子图状态（输入 + 输出）。"""
    pass


__all__ = [
    "SubGoodsReceiptStateField",
    "SubGoodsReceiptInputState",
    "SubGoodsReceiptOutputState",
    "SubGoodsReceiptState",
    "ScmConfig",
]
