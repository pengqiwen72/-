from enum import Enum

from typing_extensions import Literal
from langgraph.types import Command
from utils.agent_logger import get_logger
from langchain_core.runnables import RunnableConfig
from graphs.scm_graph.scm_state import ScmState, ScmStateField
from models.scm_models import Intent, NodeResult
from llm.llm_intent_detect import llm_intent_detect

logger = get_logger("scm_node")

# 节点名称
class NodeName(str, Enum):
    # 主图节点
    CLEANUP_STATE       = "cleanup_state"
    WELCOME_MESSAGE     = "scm_welcome_message"
    INTENT_DETECTION    = "intent_detection"
    TASK_RESULT_CONFIRM = "task_result_confirm"

    # 子图节点（意图识别后，根据意图调用对应的子图）
    MATERIAL_QA         = "material_qa"         ## 物料与制度问答（RAG + 图谱双通道）
    PURCHASE_REQUEST    = "purchase_request"    ## 采购申请（策略引擎 + HITL 审批 + 幂等）
    GOODS_RECEIPT       = "goods_receipt"       ## 收货验收（质检判定 + 超阈值人工复核）
    INVENTORY_QUERY     = "inventory_query"     ## 库存管理（多仓分布 / 安全库存 / 调拨建议）
    SUPPLIER_QUERY      = "supplier_query"      ## 供应商管理（查询 / 比价 / 履约评分）
    NO_CLEAR_INTENTION  = "no_clear_intention"  ## 未明确意图

# 意图列表（意图识别后，根据意图调用对应的子图）
class IntentDetection(str, Enum):
    MATERIAL_QA        = NodeName.MATERIAL_QA.value
    PURCHASE_REQUEST   = NodeName.PURCHASE_REQUEST.value
    GOODS_RECEIPT      = NodeName.GOODS_RECEIPT.value
    INVENTORY_QUERY    = NodeName.INVENTORY_QUERY.value
    SUPPLIER_QUERY     = NodeName.SUPPLIER_QUERY.value
    NO_CLEAR_INTENTION = NodeName.NO_CLEAR_INTENTION.value

def cleanup_state_node(state: ScmState, config: RunnableConfig):
    """清理状态节点"""
    logger.info("=== 清理状态 ===")
    return {
        ## 执行结果
        ScmStateField.NODE_RESULT: None,
        ScmStateField.TASK_RESULT: None,
        ## 信息Load
        ScmStateField.OPERATOR_INFO: None,
        ScmStateField.MATERIALS: None,
        ScmStateField.SUPPLIERS: None,
        ScmStateField.WAREHOUSES: None,
        ScmStateField.STOCK_ITEMS: None,
        ScmStateField.PURCHASE_ORDERS: None,
        ## Intermediate Output 中间输出
        ScmStateField.MATERIAL_QA: None,
        ScmStateField.PURCHASE_REQUEST: None,
        ScmStateField.GOODS_RECEIPT: None,
        ScmStateField.INVENTORY_QUERY: None,
        ScmStateField.SUPPLIER_QUERY: None,
    }

def welcome_message(state: ScmState, config: RunnableConfig):
    """欢迎消息节点"""

    logger.info("=== 欢迎消息 ===")

    return {
        ScmStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="您好，我是供应链采购仓储智能助手，很高兴为您服务",
            execute_exceptions=[]
        )]
    }

def intent_detection(state: ScmState, config: RunnableConfig) -> Command[Literal[*[intent.value for intent in IntentDetection]]]:
    """意图检测节点"""

    logger.info("=== 意图检测 ===")

    intents=[
        Intent(intent_id=IntentDetection.MATERIAL_QA.value, intent_description="需要查询物料规格参数、替代料，或采购制度、验收标准、操作流程等有依据的问题"),
        Intent(intent_id=IntentDetection.PURCHASE_REQUEST.value, intent_description="要发起采购申请、补货下单，需要走审批与下单流程"),
        Intent(intent_id=IntentDetection.GOODS_RECEIPT.value, intent_description="到货了要登记收货、验收质检，或反馈到货不良、破损、退货"),
        Intent(intent_id=IntentDetection.INVENTORY_QUERY.value, intent_description="要查询库存量、多仓分布、安全库存预警、调拨建议"),
        Intent(intent_id=IntentDetection.SUPPLIER_QUERY.value, intent_description="要查供应商信息、比价、报价、交期与履约评分"),
        Intent(intent_id=IntentDetection.NO_CLEAR_INTENTION.value, intent_description="我想咨询一些问题，或者没有明确的意图，仅仅是聊聊天")
    ]

    task_input = state.get(ScmStateField.TASK_INPUT, "")

    # 第一层：枚举精确匹配 —— 任务值直接等于意图名，零成本直达，不调模型
    if task_input in [intent.value for intent in IntentDetection]:
        logger.info(f"意图枚举命中: {task_input}")
        return Command(goto=task_input)

    # 第二层：语义识别 —— 自然语言描述交给模型判断
    intent_id = IntentDetection.NO_CLEAR_INTENTION.value
    try:
        intent = llm_intent_detect(task_input, intents)
        intent_id = intent.intent_id
    except Exception as e:
        logger.warning(f"语义意图识别失败，回落未明确意图: {e}")

    # 第三层：兜底 —— 模型给了不在枚举里的值，同样归入通用问答
    if not intent_id in [intent.value for intent in IntentDetection]:
        intent_id = IntentDetection.NO_CLEAR_INTENTION.value

    logger.info(f"意图识别结果: {intent_id}")
    return Command(goto = intent_id)


# 任务结果确认
def task_result_confirm(state: ScmState, config: RunnableConfig):
    """任务结果确认节点"""

    logger.info("=== 任务结果确认 ===")

    return {
        ScmStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.TASK_RESULT_CONFIRM.value,
            execute_result_code=0,
            execute_result_msg="任务已完成，希望您能满意",
            execute_exceptions=[]
        )]
    }