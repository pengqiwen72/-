"""
采购申请子图的状态定义。

字段口径
--------
* 输入要四样：申请原文（`task_input`）+ 候选物料/仓库（抽取时给模型做候选）
  + 提报人（进单号，也是审批记录里的操作员）。全部来自主图装载好的统一状态，
  子图自己不查库。
* 输出 `purchase_request` 是 `PurchasePlan`（pydantic 模型）。注意不能直接把
  抽取阶段的 `PurchaseDraft` 写进状态：草稿是中间产物，方案才是链路的结论；
  混在一起会让下游分不清「用户说了什么」和「系统决定怎么做」。

字段名与菜单 id 对齐（`purchase_request`）——「状态里哪块被填了」就等于
「哪个子图跑过了」，测试与排障都靠这个约定。
"""

import operator
from enum import Enum
from typing import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmConfig, ScmStateField
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    OperatorInfo,
    PurchasePlan,
    SupplierInfo,
    TaskResult,
    WarehouseInfo,
)


class SubPurchaseRequestStateField(str, Enum):
    # ---- 输入 ----
    TASK_INPUT   = ScmStateField.TASK_INPUT.value
    MATERIALS    = ScmStateField.MATERIALS.value
    SUPPLIERS    = ScmStateField.SUPPLIERS.value
    WAREHOUSES   = ScmStateField.WAREHOUSES.value
    OPERATOR_INFO = ScmStateField.OPERATOR_INFO.value
    # ---- 输出 ----
    PURCHASE_REQUEST = ScmStateField.PURCHASE_REQUEST.value
    TASK_RESULT      = ScmStateField.TASK_RESULT.value
    NODE_RESULT      = ScmStateField.NODE_RESULT.value


class SubPurchaseRequestInputState(TypedDict):
    task_input:    str
    materials:     List[MaterialInfo]
    suppliers:     List[SupplierInfo]
    warehouses:    List[WarehouseInfo]
    operator_info: OperatorInfo


class SubPurchaseRequestOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    purchase_request: PurchasePlan


class SubPurchaseRequestState(SubPurchaseRequestInputState, SubPurchaseRequestOutputState):
    """完整的子图状态（输入 + 输出）。"""
    pass


__all__ = [
    "SubPurchaseRequestStateField",
    "SubPurchaseRequestInputState",
    "SubPurchaseRequestOutputState",
    "SubPurchaseRequestState",
    "ScmConfig",
]
