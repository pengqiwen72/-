"""
store 到业务模型的取数层。

为什么中间隔这一层
------------------
装载节点只认「函数返回模型」，不关心数据躺在哪个命名空间、字段叫什么名字。
存储换布局（命名空间调整、外部接口顶替 store）时改这一层就够了，
节点与子图保持不动。错误处理也统一在这里：查不到就返回空模型/空列表 +
一条 error 日志，让上层的「数据缺失」分支有稳定的输入形状。
"""

from typing import List

from models.scm_models import (
    MaterialInfo,
    OperatorInfo,
    PurchaseOrderInfo,
    StockItem,
    SupplierInfo,
    WarehouseInfo,
)
from store.store_client import StoreClient
from utils.agent_logger import get_logger
from utils.debug_aspect import debug

store_client = StoreClient()

logger = get_logger("store_tool")


@debug
def get_operator_info(user_id: str) -> OperatorInfo:
    """获取操作员信息（user_id 即 operator_id，如 OP001）"""

    record = store_client.get_operator_by_id(user_id)

    if not record:
        logger.error(f"获取操作员信息失败: {user_id}")
        return OperatorInfo()

    return OperatorInfo.model_validate(record)


@debug
def get_materials() -> List[MaterialInfo]:
    """物料主数据全量"""
    return [MaterialInfo.model_validate(r) for r in store_client.list_all_materials()]


@debug
def get_suppliers() -> List[SupplierInfo]:
    """供应商主数据全量"""
    return [SupplierInfo.model_validate(r) for r in store_client.list_all_suppliers()]


@debug
def get_warehouses() -> List[WarehouseInfo]:
    """仓库主数据全量"""
    return [WarehouseInfo.model_validate(r) for r in store_client.list_all_warehouses()]


@debug
def get_stock_items() -> List[StockItem]:
    """库存快照全量（物料 × 仓库）"""
    return [StockItem.model_validate(r) for r in store_client.list_all_stock()]


@debug
def get_purchase_orders() -> List[PurchaseOrderInfo]:
    """采购订单全量（含在途）。子图按需再筛状态。"""
    return [PurchaseOrderInfo.model_validate(r) for r in store_client.list_all_purchase_orders()]
