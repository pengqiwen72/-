from langgraph.graph import START, END, StateGraph
from graphs.scm_graph.scm_state import ScmState, InputState, OutputState, ScmConfig

from graphs.scm_graph.sub_material_qa.sub_material_qa_graph import sub_material_qa_graph
from graphs.scm_graph.sub_purchase_request.sub_purchase_request_graph import sub_purchase_request_graph
from graphs.scm_graph.sub_goods_receipt.sub_goods_receipt_graph import sub_goods_receipt_graph
from graphs.scm_graph.sub_inventory.sub_inventory_graph import sub_inventory_graph
from graphs.scm_graph.sub_supplier.sub_supplier_graph import sub_supplier_graph
from graphs.scm_graph.sub_talk.sub_talk_graph import sub_talk_graph
from graphs.scm_graph.scm_node import NodeName, intent_detection, task_result_confirm, welcome_message, cleanup_state_node
from graphs.scm_graph.scm_node_load_data import NodeName as LoadDataNodeName
from graphs.scm_graph.scm_node_load_data import (
    load_welcome_message_node,
    load_operator_info_node,
    load_materials_node,
    load_suppliers_node,
    load_warehouses_node,
    load_stock_node,
    load_purchase_orders_node,
)

import os
from utils.env_loader import load_agent_env

# ⚠️ 必须走统一入口：本模块是在「请求上下文里」才被首次 import 的（工作台首请求
# 才建图），裸调 load_dotenv() 会撞 langgraph dev 的 blockbuster 守卫，整条请求 500。
# 详见 utils/env_loader.py 的模块说明。
load_agent_env()

from utils.agent_logger import get_logger

logger = get_logger("scm_graph")

builder = StateGraph(state_schema=ScmState, input_schema=InputState, output_schema=OutputState, config_schema=ScmConfig)

builder.add_node(NodeName.CLEANUP_STATE.value, cleanup_state_node) ## 清理状态
builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message) ## 欢迎消息
builder.add_node(NodeName.INTENT_DETECTION.value, intent_detection) ## 意图检测
builder.add_node(NodeName.TASK_RESULT_CONFIRM.value, task_result_confirm) ## 任务结果确认

## 加载数据节点（7 路，直线串行）
builder.add_node(LoadDataNodeName.LOAD_WELCOME_MESSAGE.value, load_welcome_message_node) ## 欢迎语
builder.add_node(LoadDataNodeName.LOAD_OPERATOR_INFO.value, load_operator_info_node) ## 操作员信息
builder.add_node(LoadDataNodeName.LOAD_MATERIALS.value, load_materials_node) ## 物料主数据
builder.add_node(LoadDataNodeName.LOAD_SUPPLIERS.value, load_suppliers_node) ## 供应商主数据
builder.add_node(LoadDataNodeName.LOAD_WAREHOUSES.value, load_warehouses_node) ## 仓库主数据
builder.add_node(LoadDataNodeName.LOAD_STOCK.value, load_stock_node) ## 库存快照
builder.add_node(LoadDataNodeName.LOAD_PURCHASE_ORDERS.value, load_purchase_orders_node) ## 采购订单

## 业务子图节点
builder.add_node(NodeName.MATERIAL_QA.value, sub_material_qa_graph) ## 物料与制度问答（RAG + 图谱双通道）
builder.add_node(NodeName.PURCHASE_REQUEST.value, sub_purchase_request_graph) ## 采购申请（策略 + HITL 审批 + 幂等）
builder.add_node(NodeName.GOODS_RECEIPT.value, sub_goods_receipt_graph) ## 收货验收（质检 + 超阈值复核）
builder.add_node(NodeName.INVENTORY_QUERY.value, sub_inventory_graph) ## 库存管理（多仓分布 / 安全库存）
builder.add_node(NodeName.SUPPLIER_QUERY.value, sub_supplier_graph) ## 供应商管理（比价 / 履约评分）
builder.add_node(NodeName.NO_CLEAR_INTENTION.value, sub_talk_graph) ## 未明确意图

## 加载数据节点执行完成后，调用意图检测节点
builder.add_edge(START, NodeName.CLEANUP_STATE.value)
builder.add_edge(NodeName.CLEANUP_STATE.value, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, LoadDataNodeName.LOAD_WELCOME_MESSAGE.value)
builder.add_edge(LoadDataNodeName.LOAD_WELCOME_MESSAGE.value, LoadDataNodeName.LOAD_OPERATOR_INFO.value)
builder.add_edge(LoadDataNodeName.LOAD_OPERATOR_INFO.value, LoadDataNodeName.LOAD_MATERIALS.value)
builder.add_edge(LoadDataNodeName.LOAD_MATERIALS.value, LoadDataNodeName.LOAD_SUPPLIERS.value)
builder.add_edge(LoadDataNodeName.LOAD_SUPPLIERS.value, LoadDataNodeName.LOAD_WAREHOUSES.value)
builder.add_edge(LoadDataNodeName.LOAD_WAREHOUSES.value, LoadDataNodeName.LOAD_STOCK.value)
builder.add_edge(LoadDataNodeName.LOAD_STOCK.value, LoadDataNodeName.LOAD_PURCHASE_ORDERS.value)
builder.add_edge(LoadDataNodeName.LOAD_PURCHASE_ORDERS.value, NodeName.INTENT_DETECTION.value)

## 意图检测后按意图路由到对应子图（枚举直达 / 语义 / 兜底三层，见 scm_node.py）；
## 六个子图执行完成后统一到 task_result_confirm 收尾。

builder.add_edge(NodeName.MATERIAL_QA.value,        NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.PURCHASE_REQUEST.value,   NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.GOODS_RECEIPT.value,      NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.INVENTORY_QUERY.value,    NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.SUPPLIER_QUERY.value,     NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.NO_CLEAR_INTENTION.value, NodeName.TASK_RESULT_CONFIRM.value)
builder.add_edge(NodeName.TASK_RESULT_CONFIRM.value, END)

def _get_callbacks():
    """按配置返回链路追踪回调（未启用则返回空列表）。"""
    if os.getenv("LANGFUSE_ENABLED", "false").lower() == "true":
        try:
            from langfuse.langchain import CallbackHandler

            return [CallbackHandler()]
        except Exception as e:  # pragma: no cover - 追踪不可用不应阻断主流程
            logger.warning(f"Langfuse 回调初始化失败，跳过追踪：{e}")
    return []


def build_graph(*, checkpointer=None, store=None):
    """
    编译主图。

    显式传入 checkpointer / store 时，图可以在**脱离 LangGraph 平台运行时**的
    场景下工作（离线 demo、pytest）。不传时保持原有行为，由平台注入。

    平台模式说明：langgraph dev 会在装载时用服务端自己的 checkpointer/store
    覆盖图上的实例，所以这里给模块级 graph 挂上 InMemory 实现不会影响线上。
    """
    compiled = builder.compile(checkpointer=checkpointer, store=store)

    callbacks = _get_callbacks()
    if callbacks:
        compiled = compiled.with_config({"callbacks": callbacks})
    return compiled


# 模块级图：供 langgraph.json 注册（平台运行时）
graph = build_graph()