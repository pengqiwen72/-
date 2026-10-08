"""
主图的状态定义。

状态分三层，对应主图的三个阶段
--------------------------------
* **输入**（`InputState`）：`task_input` 一句话诉求（或菜单 id）。
* **中间输入**（`IntermediateInputState`）：数据装载阶段汇入的业务数据 ——
  操作员、物料、供应商、仓库、库存、在途采购单。**统一状态模型汇多源数据**：
  装载节点把 store 里各命名空间的数据拉成模型，子图只读状态，不再各自查库。
  好处有两个：子图是纯函数式的（给定状态必有确定输出，好测），以及
  换了数据源（mock → 真实接口）只需要改装载节点，子图一行不动。
* **中间输出 + 输出**（`IntermediateOutputState` / `OutputState`）：各子图的业务
  产出与最终结果。**中间输出的字段名与意图/菜单 id 一一对应**（`material_qa` /
  `purchase_request` / …）：路由到哪个子图，就只有哪个字段被填 —— 反过来看状态
  就能知道这次走了哪条链路，测试与前端都依赖这条对应关系。

⚠️ 子图的 `output_schema` 是一道**过滤器**：子图要回写到主图的字段必须出现在
主图状态里（这里），且出现在子图自己的 output_schema 里。少一处，字段就被静默
丢弃 —— 不报错，只是「跑完了但什么都没发生」。
"""

import operator
from typing_extensions import Annotated, List, TypedDict
from enum import Enum
from models.scm_models import (
    InventoryReport,
    MaterialInfo,
    MaterialQA,
    NodeResult,
    OperatorInfo,
    PurchaseOrderInfo,
    PurchasePlan,
    ReceiptPlan,
    StockItem,
    SupplierComparison,
    SupplierInfo,
    TaskResult,
    WarehouseInfo,
)


def reducer_node_result(current: List[NodeResult], update: List[NodeResult]) -> List[NodeResult]:
    """ 自定义node_result的reducer，用于合并node_result """
    # 如果update为空，则置空node_result
    if not update:
        return []

    #否则，追加
    return current + update

class InputState(TypedDict):
    '''
    主图的输入State
    '''
    task_input: str


class OutputState(TypedDict):
    '''
    主图的输出State
    '''
    task_result:        TaskResult
    node_result:        Annotated[List[NodeResult], reducer_node_result]

class IntermediateInputState(TypedDict):
    '''
    数据装载阶段汇入的业务数据（只读）；子图从这里取数，不各自查库
    '''
    operator_info:      OperatorInfo
    materials:          List[MaterialInfo]
    suppliers:          List[SupplierInfo]
    warehouses:         List[WarehouseInfo]
    stock_items:        List[StockItem]
    purchase_orders:    List[PurchaseOrderInfo]

class IntermediateOutputState(TypedDict):
    '''
    各子图的业务产出；字段名与意图（菜单 id）一一对应
    '''
    material_qa:        MaterialQA
    purchase_request:   PurchasePlan
    goods_receipt:      ReceiptPlan
    inventory_query:    InventoryReport
    supplier_query:     SupplierComparison

class ScmState(InputState,IntermediateInputState,IntermediateOutputState,OutputState):
    '''
    主图的State，继承InputState,IntermediateInputState,IntermediateOutputState,OutputState
    '''
    pass

class ScmStateField(str, Enum):
    '''
    主图的State字段名称定义
    '''

    ## 任务输入
    TASK_INPUT          ="task_input"

    ## 信息Load
    OPERATOR_INFO       ="operator_info"
    MATERIALS           ="materials"
    SUPPLIERS           ="suppliers"
    WAREHOUSES          ="warehouses"
    STOCK_ITEMS         ="stock_items"
    PURCHASE_ORDERS     ="purchase_orders"

    ## Intermediate Output 中间输出
    MATERIAL_QA         ="material_qa"
    PURCHASE_REQUEST    ="purchase_request"
    GOODS_RECEIPT       ="goods_receipt"
    INVENTORY_QUERY     ="inventory_query"
    SUPPLIER_QUERY      ="supplier_query"

    ## 执行结果
    TASK_RESULT         ="task_result"
    NODE_RESULT         ="node_result"


class ScmConfig(TypedDict):
    '''
    主图的Config
    '''
    user_id:      str
    external_id:  str


class ConfigurableField(str, Enum):
    '''
    主图的配置字段名称定义
    '''
    configurable      ="configurable"
    user_id           ="user_id"
    external_id       ="external_id"

