"""
端口到业务模型的取数层（装载链路）。

为什么中间隔这一层
------------------
装载节点只认「函数返回模型」，不关心数据从哪来、上游字段叫什么。数据来源是
**业务端口**（mock 读种子并按上游报文形状重组 / http 调真实系统 / mcp 经
网关）—— 装载链路与子图因此走同一扇门：`TOOL_GATEWAY=direct|mcp` 一换，
**整条装载链路**也跟着换到网关上去，而不是只有子图经网关。

取数 → 归一（integrations/normalize.py）→ 校验成模型，三步在这里收口。
字段口径（matCode → material_code、refPrice 分 → 元、orderStatus=30 →
received）全部由归一层的显式一步完成，本层只负责把结果套进模型。

端口抛异常时**不吞**：装载节点按域 try/except，失败域记一条 code=1 的
node_result 并给该域空值 —— 「这个数据没查到」的分支在节点层，不在这层。
"""

from typing import List

from integrations.factory import (
    get_inventory_port,
    get_material_port,
    get_purchase_port,
    get_supplier_port,
    get_warehouse_port,
)
from integrations.normalize import (
    normalize_material,
    normalize_purchase_order,
    normalize_stock,
    normalize_supplier,
    normalize_warehouse,
)
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
    """获取操作员信息（user_id 即 operator_id，如 OP001）。

    操作员是本地账号体系数据，不在 ERP/SRM/WMS 任何一家的接口面上，
    所以这一路仍从 store 读 —— 端口只对齐真实业务系统的边界。
    """

    record = store_client.get_operator_by_id(user_id)

    if not record:
        logger.error(f"获取操作员信息失败: {user_id}")
        return OperatorInfo()

    return OperatorInfo.model_validate(record)


@debug
def get_materials() -> List[MaterialInfo]:
    """物料主数据全量（ERP 报文 → 归一 → 模型）"""
    return [
        MaterialInfo.model_validate(normalize_material(r))
        for r in get_material_port().list_materials()
    ]


@debug
def get_suppliers() -> List[SupplierInfo]:
    """供应商主数据全量（SRM 报文 → 归一 → 模型）"""
    return [
        SupplierInfo.model_validate(normalize_supplier(r))
        for r in get_supplier_port().list_suppliers()
    ]


@debug
def get_warehouses() -> List[WarehouseInfo]:
    """仓库主数据全量（WMS 报文 → 归一 → 模型）"""
    return [
        WarehouseInfo.model_validate(normalize_warehouse(r))
        for r in get_warehouse_port().list_warehouses()
    ]


@debug
def get_stock_items() -> List[StockItem]:
    """库存快照全量（WMS 报文 → 归一 → 模型；可用量由 StockItem 属性算）"""
    return [
        StockItem.model_validate(normalize_stock(r))
        for r in get_inventory_port().list_stock()
    ]


@debug
def get_purchase_orders() -> List[PurchaseOrderInfo]:
    """采购订单全量（ERP 报文 → 归一 → 模型；含在途，子图按需再筛状态）"""
    return [
        PurchaseOrderInfo.model_validate(normalize_purchase_order(r))
        for r in get_purchase_port().list_purchase_orders()
    ]
