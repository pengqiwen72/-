"""
数据装载节点（7 个，直线串行）。

为什么要在意图识别之前把数据全查出来
------------------------------------
两句话：**统一状态模型汇多源数据**，以及**把「取数」与「用数」分开**。

* 子图只读状态，不各自查库 —— 子图因此是「给定状态必有确定输出」的纯逻辑，
  演示时每一步都能解释数字从哪来，测试也能只喂状态不给 store。
* 换了数据源（mock → 真实 WMS/ERP 接口），改装载节点即可，子图一行不动；
  反过来，如果每个子图自己查库，换数据源要去五个地方改。

7 个节点刻意拆开而不是合成一个函数：状态里多了一个数据域、
执行轨迹里多一行、工作台时间线上多一个阶段 —— 「汇了哪些源」是看得见的。

装载失败**不**中断主流程：该域返回空值 + 一条 code=1 的 node_result，
子图对空状态有明确分支（如实告知「这个数据没查到」），
比整条链路 500 更符合业务系统的行为。
"""

from langchain_core.runnables import RunnableConfig
from graphs.scm_graph.scm_state import ScmState, ScmStateField, ConfigurableField
from tools.store_tool import (
    get_materials,
    get_operator_info,
    get_purchase_orders,
    get_stock_items,
    get_suppliers,
    get_warehouses,
)
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    OperatorInfo,
    PurchaseOrderInfo,
    StockItem,
    SupplierInfo,
    WarehouseInfo,
)
from utils.agent_logger import get_logger
from enum import Enum

logger = get_logger("scm_node_load_data")

class NodeName(str, Enum):
    LOAD_WELCOME_MESSAGE    = "load_welcome_message_node"
    LOAD_OPERATOR_INFO      = "load_operator_info_node"
    LOAD_MATERIALS          = "load_materials_node"
    LOAD_SUPPLIERS          = "load_suppliers_node"
    LOAD_WAREHOUSES         = "load_warehouses_node"
    LOAD_STOCK              = "load_stock_node"
    LOAD_PURCHASE_ORDERS    = "load_purchase_orders_node"


def _loaded(node: NodeName, field: ScmStateField, value, message: str):
    """成功装载的返回结构。"""
    return {
        field: value,
        ScmStateField.NODE_RESULT: [NodeResult(
            execute_node_name=node.value,
            execute_result_code=0,
            execute_result_msg=message,
            execute_exceptions=[]
        )]
    }


def _failed(node: NodeName, field: ScmStateField, fallback, message: str, error: Exception):
    """装载失败的返回结构：空值 + code=1，不抛。"""
    logger.error(f"{message}: {error}")
    return {
        field: fallback,
        ScmStateField.NODE_RESULT: [NodeResult(
            execute_node_name=node.value,
            execute_result_code=1,
            execute_result_msg=message,
            execute_exceptions=[str(error)]
        )]
    }


def load_welcome_message_node(state: ScmState, config: RunnableConfig):
    """欢迎信息节点"""

    logger.info("=== 欢迎信息 ===")

    return {
        ScmStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.LOAD_WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在为您加载业务数据，请稍等。",
            execute_exceptions=[]
        )]
    }


def load_operator_info_node(state: ScmState, config: RunnableConfig):
    """加载操作员信息节点"""

    logger.info("=== 加载操作员信息 ===")

    user_id = config[ConfigurableField.configurable][ConfigurableField.user_id]

    try:
        operator_info: OperatorInfo = get_operator_info(user_id=user_id)
        return _loaded(NodeName.LOAD_OPERATOR_INFO, ScmStateField.OPERATOR_INFO,
                       operator_info, f"完成操作员信息加载（{operator_info.name or user_id}）")
    except Exception as e:
        return _failed(NodeName.LOAD_OPERATOR_INFO, ScmStateField.OPERATOR_INFO,
                       OperatorInfo(), "加载操作员信息失败", e)


def load_materials_node(state: ScmState, config: RunnableConfig):
    """加载物料主数据节点"""

    logger.info("=== 加载物料主数据 ===")

    try:
        materials = get_materials()
        return _loaded(NodeName.LOAD_MATERIALS, ScmStateField.MATERIALS,
                       materials, f"完成物料主数据加载（{len(materials)} 条）")
    except Exception as e:
        return _failed(NodeName.LOAD_MATERIALS, ScmStateField.MATERIALS,
                       [], "加载物料主数据失败", e)


def load_suppliers_node(state: ScmState, config: RunnableConfig):
    """加载供应商主数据节点"""

    logger.info("=== 加载供应商主数据 ===")

    try:
        suppliers = get_suppliers()
        return _loaded(NodeName.LOAD_SUPPLIERS, ScmStateField.SUPPLIERS,
                       suppliers, f"完成供应商主数据加载（{len(suppliers)} 条）")
    except Exception as e:
        return _failed(NodeName.LOAD_SUPPLIERS, ScmStateField.SUPPLIERS,
                       [], "加载供应商主数据失败", e)


def load_warehouses_node(state: ScmState, config: RunnableConfig):
    """加载仓库主数据节点"""

    logger.info("=== 加载仓库主数据 ===")

    try:
        warehouses = get_warehouses()
        return _loaded(NodeName.LOAD_WAREHOUSES, ScmStateField.WAREHOUSES,
                       warehouses, f"完成仓库主数据加载（{len(warehouses)} 条）")
    except Exception as e:
        return _failed(NodeName.LOAD_WAREHOUSES, ScmStateField.WAREHOUSES,
                       [], "加载仓库主数据失败", e)


def load_stock_node(state: ScmState, config: RunnableConfig):
    """加载库存快照节点"""

    logger.info("=== 加载库存快照 ===")

    try:
        stock_items = get_stock_items()
        below = sum(1 for s in stock_items if s.below_safety)
        return _loaded(NodeName.LOAD_STOCK, ScmStateField.STOCK_ITEMS,
                       stock_items,
                       f"完成库存快照加载（{len(stock_items)} 个库存点，{below} 个低于安全线）")
    except Exception as e:
        return _failed(NodeName.LOAD_STOCK, ScmStateField.STOCK_ITEMS,
                       [], "加载库存快照失败", e)


def load_purchase_orders_node(state: ScmState, config: RunnableConfig):
    """加载采购订单节点"""

    logger.info("=== 加载采购订单 ===")

    try:
        orders = get_purchase_orders()
        in_transit = sum(1 for o in orders if o.status == "in_transit")
        return _loaded(NodeName.LOAD_PURCHASE_ORDERS, ScmStateField.PURCHASE_ORDERS,
                       orders,
                       f"完成采购订单加载（{len(orders)} 条，{in_transit} 条在途）")
    except Exception as e:
        return _failed(NodeName.LOAD_PURCHASE_ORDERS, ScmStateField.PURCHASE_ORDERS,
                       [], "加载采购订单失败", e)
