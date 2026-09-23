"""
商品咨询问答子图的两个节点。

链路：确定问题 → 混合召回 → 重排 → 置信度判定 → 作答 / 转人工。

**问题从哪来**
坐席在工作台点「商品咨询」按钮时，`task_input` 传的是意图枚举值 `product_qa`
本身；客户直接发问、或语义路由命中时，`task_input` 就是问题原文。两种入口
都收敛到 `_resolve_question()`：按钮只表达「我要这个能力」，问题永远取自真实
会话数据。这个分工在业务系统里是常态——UI 传的是动作，数据从状态里取。
"""

from enum import Enum

from langchain_core.runnables import RunnableConfig

from graphs.cs_graph.cs_node import IntentDetection
from graphs.cs_graph.sub_product_qa.sub_product_qa_state import (
    SubProductQAState,
    SubProductQAStateField,
)
from llm.llm_setting import is_offline_model
from models.cs_models import NodeResult, ProductQA, TaskResult
from observability.progress import emit, node_done
from rag.expansion import expand_query
from rag.index import get_knowledge_base
from utils.agent_logger import get_logger
from utils.text import last_question_clause

logger = get_logger("sub_product_qa_node")

#: 会话记录里标识「客户」的发送者名（由 `tools.store_tool.get_chat_history` 归一）
CUSTOMER_SENDER = "客户"


class NodeName(str, Enum):
    WELCOME_MESSAGE = "product_qa_welcome_node"
    RETRIEVE_ANSWER = "product_qa_retrieve_node"


# ---------------------------------------------------------------- 问题定位

def _last_customer_message(state: SubProductQAState) -> str:
    """取会话里客户最后一条消息，并抽出其中真正在问的诉求。

    取**一条消息里的末尾疑问句串**而不是整段会话：客户前面聊过别的话题
    （下单、破损、键盘连不连 Mac），整段丢给检索会把查询稀释掉，命中的条款
    跟着跑偏。抽句规则与话术链路共用 `utils.text.last_question_clause`，
    两个链路对「客户在问什么」的判断必须一致。
    """
    history = state.get(SubProductQAStateField.CHAT_HISTORY)
    msgs = getattr(history, "chat_msgs", None) or []

    for msg in reversed(msgs):
        if getattr(msg, "sender", "") != CUSTOMER_SENDER:
            continue
        content = (getattr(msg, "content", "") or "").strip()
        if content:
            return last_question_clause(content)
    return ""


def _resolve_question(state: SubProductQAState) -> str:
    """确定这次要回答的问题。"""
    raw = (state.get(SubProductQAStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.PRODUCT_QA.value:
        return raw
    return _last_customer_message(state)


# ---------------------------------------------------------------- 生成器

def _generator():
    """在线模式返回生成器，离线模式返回 None（走确定性转述）。

    离线模型不产出自然语言，硬调它只会拿到一段 JSON 文本。真正的分界不是
    「有没有配 API Key」，而是「这个模型会不会说人话」，所以按实例判断。
    """
    if is_offline_model():
        return None

    from llm.llm_rag_answer import llm_rag_answer

    return llm_rag_answer


# ---------------------------------------------------------------- 节点

def welcome_message_node(state: SubProductQAState, config: RunnableConfig):
    """检索开始前的提示节点。"""
    logger.info("=== 商品咨询 ===")

    emit("retrieve_start", detail="准备检索商品知识库")

    return {
        SubProductQAStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在检索商品资料，请稍等。",
            execute_exceptions=[]
        )]
    }


def retrieve_answer_node(state: SubProductQAState, config: RunnableConfig):
    """检索 + 作答节点。"""
    logger.info("=== 检索并作答 ===")

    question = _resolve_question(state)
    if not question:
        logger.warning("没有可用的问题：task_input 是意图名且会话里没有客户消息")
        return {
            SubProductQAStateField.PRODUCT_QA: ProductQA(
                question="",
                answer="没有识别到需要咨询的问题，请补充一下您想了解的商品或政策。",
                status="handoff",
                reason="问题为空：既没有传入问题原文，会话里也没有客户消息",
            ),
            SubProductQAStateField.TASK_RESULT: TaskResult(
                task_result="没有识别到需要咨询的问题，请补充一下您想了解的商品或政策。",
                task_result_explain="问题为空",
                task_result_code=1
            ),
            SubProductQAStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.RETRIEVE_ANSWER.value,
                execute_result_code=1,
                execute_result_msg="问题为空，无法检索",
                execute_exceptions=["task_input 与会话记录都没有取到问题"]
            )]
        }

    _, injected = expand_query(question)
    if injected:
        emit("retrieve_start", detail=f"口语「{question[:20]}」→ 知识库术语 {'、'.join(injected)}")

    try:
        kb = get_knowledge_base()
        # generator 交给知识库在「判定不需要转人工」之后才调用，
        # 避免在注定要转人工的问题上白调一次大模型
        rag_answer = kb.ask(question, generator=_generator())
    except Exception as e:
        logger.error(f"商品咨询检索失败: {e}")
        return {
            SubProductQAStateField.PRODUCT_QA: ProductQA(
                question=question,
                answer="检索商品资料时出错了，我已经为您转接人工客服。",
                status="handoff",
                reason=f"检索链路异常：{e}",
            ),
            SubProductQAStateField.TASK_RESULT: TaskResult(
                task_result="很抱歉，查询商品资料时遇到了问题。",
                task_result_explain="检索失败",
                task_result_code=1
            ),
            SubProductQAStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.RETRIEVE_ANSWER.value,
                execute_result_code=1,
                execute_result_msg="检索商品资料失败",
                execute_exceptions=[str(e)]
            )]
        }

    product_qa = ProductQA.from_rag_answer(question, rag_answer)

    # 进度事件：命中了几条、置信度多少、走没走转人工，全部外显。
    # demo 与 SSE 靠这组事件解释「为什么这次答了、那次转了人工」。
    emit(
        "retrieve_done",
        detail=f"命中 {len(product_qa.citations)} 条依据，置信度 {product_qa.confidence:.2f}",
        confidence=product_qa.confidence,
        citations=len(product_qa.citations),
        matched_terms=product_qa.matched_terms,
    )
    if product_qa.degraded:
        emit("rerank_done", detail="模型输出未通过数字校验，已回退为原文转述")
    if product_qa.is_handoff:
        emit("handoff", detail=product_qa.reason or "置信度不足，转人工")

    node_done(NodeName.RETRIEVE_ANSWER.value, f"置信度 {product_qa.confidence:.2f}")

    logger.info(f"回答：{product_qa.answer}")

    # 转人工是**正常业务结果**而不是失败：链路跑完了，结论是「该由人来接」，
    # 所以 task_result_code 仍为 0（0 表示「结果有效」）。判失败会让上层把
    # 一次正确的兜底当成 bug。
    return {
        SubProductQAStateField.PRODUCT_QA: product_qa,
        SubProductQAStateField.TASK_RESULT: TaskResult(
            task_result=product_qa.render(),
            task_result_explain=(
                f"转人工：{product_qa.reason}" if product_qa.is_handoff
                else f"基于 {len(product_qa.citations)} 条知识库依据作答"
            ),
            task_result_code=0
        ),
        SubProductQAStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.RETRIEVE_ANSWER.value,
            execute_result_code=0,
            execute_result_msg=(
                "未找到依据，已转人工" if product_qa.is_handoff
                else f"作答成功（置信度 {product_qa.confidence:.2f}）"
            ),
            execute_exceptions=[]
        )]
    }
