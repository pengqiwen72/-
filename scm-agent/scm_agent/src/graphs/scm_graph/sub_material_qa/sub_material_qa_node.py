"""
物料与制度问答子图的两个节点。

链路：确定问题 → 混合召回 → 重排 → 置信度判定 → 作答 / 转人工。

**问题从哪来**
工作台上点「智能问答」按钮时，`task_input` 传的是意图枚举值 `material_qa`
本身；用户直接输入问题、或语义路由命中时，`task_input` 就是问题原文。
按钮入口要是在这里硬编一个「示例问题」就会变成答非所问 —— 正确行为是
明确提示补充问题，把「这次调用没有携带可回答的内容」如实讲出来。
"""

from enum import Enum

from langchain_core.runnables import RunnableConfig

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.sub_material_qa.sub_material_qa_state import (
    SubMaterialQAState,
    SubMaterialQAStateField,
)
from llm.llm_setting import is_offline_model
from models.scm_models import MaterialQA, NodeResult, TaskResult
from observability.progress import emit
from rag.expansion import expand_query
from rag.index import get_knowledge_base
from utils.agent_logger import get_logger

logger = get_logger("sub_material_qa_node")


class NodeName(str, Enum):
    WELCOME_MESSAGE = "material_qa_welcome_node"
    RETRIEVE_ANSWER = "material_qa_retrieve_node"


# ---------------------------------------------------------------- 问题定位

def _resolve_question(state: SubMaterialQAState) -> str:
    """确定这次要回答的问题：不是菜单 id 的 task_input 就是问题原文。"""
    raw = (state.get(SubMaterialQAStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.MATERIAL_QA.value:
        return raw
    return ""


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

def welcome_message_node(state: SubMaterialQAState, config: RunnableConfig):
    """检索开始前的提示节点。"""
    logger.info("=== 物料与制度问答 ===")

    emit("retrieve_start", detail="准备检索物料与制度知识库")

    return {
        SubMaterialQAStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME_MESSAGE.value,
            execute_result_code=0,
            execute_result_msg="正在检索物料与制度资料，请稍等。",
            execute_exceptions=[]
        )]
    }


def retrieve_answer_node(state: SubMaterialQAState, config: RunnableConfig):
    """检索 + 作答节点。"""
    logger.info("=== 检索并作答 ===")

    question = _resolve_question(state)
    if not question:
        logger.warning("没有可用的问题：task_input 是菜单 id")
        answer = "请直接输入您要查询的问题，例如「冷轧钢板的验收标准是什么」「M100023 有没有替代料」。"
        return {
            SubMaterialQAStateField.MATERIAL_QA: MaterialQA(
                question="",
                answer=answer,
                # 沿用 handoff：对上层来说「这次没答，需要人（或用户自己）补内容」
                # 与置信度不足转人工是同一种结局，不引入第三种状态让下游分叉
                status="handoff",
                reason="按钮入口只表达「我要这个能力」，没有携带具体问题",
            ),
            SubMaterialQAStateField.TASK_RESULT: TaskResult(
                task_result=answer,
                task_result_explain="问题为空，等待用户补充",
                task_result_code=1
            ),
            SubMaterialQAStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.RETRIEVE_ANSWER.value,
                execute_result_code=1,
                execute_result_msg="问题为空，无法检索",
                execute_exceptions=["task_input 只给了菜单 id"]
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
        logger.error(f"物料与制度问答检索失败: {e}")
        return {
            SubMaterialQAStateField.MATERIAL_QA: MaterialQA(
                question=question,
                answer="检索物料与制度资料时出错了，我已将这个问题转交人工处理。",
                status="handoff",
                reason=f"检索链路异常：{e}",
            ),
            SubMaterialQAStateField.TASK_RESULT: TaskResult(
                task_result="很抱歉，查询资料时遇到了问题。",
                task_result_explain="检索失败",
                task_result_code=1
            ),
            SubMaterialQAStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.RETRIEVE_ANSWER.value,
                execute_result_code=1,
                execute_result_msg="检索资料失败",
                execute_exceptions=[str(e)]
            )]
        }

    material_qa = MaterialQA.from_rag_answer(question, rag_answer)

    # 进度事件：命中了几条、置信度多少、走没走转人工，全部外显。
    # demo 与 SSE 靠这组事件解释「为什么这次答了、那次转了人工」。
    emit(
        "retrieve_done",
        detail=f"命中 {len(material_qa.citations)} 条依据，置信度 {material_qa.confidence:.2f}",
        confidence=material_qa.confidence,
        citations=len(material_qa.citations),
        matched_terms=material_qa.matched_terms,
    )
    if material_qa.degraded:
        emit("rerank_done", detail="模型输出未通过数字校验，已回退为原文转述")
    if material_qa.is_handoff:
        emit("handoff", detail=material_qa.reason or "置信度不足，转人工")

    logger.info(f"回答：{material_qa.answer}")

    # 转人工是**正常业务结果**而不是失败：链路跑完了，结论是「该由人来接」，
    # 所以 task_result_code 仍为 0（0 表示「结果有效」）。判失败会让上层把
    # 一次正确的兜底当成 bug。
    return {
        SubMaterialQAStateField.MATERIAL_QA: material_qa,
        SubMaterialQAStateField.TASK_RESULT: TaskResult(
            task_result=material_qa.render(),
            task_result_explain=(
                f"转人工：{material_qa.reason}" if material_qa.is_handoff
                else f"基于 {len(material_qa.citations)} 条知识库依据作答"
            ),
            task_result_code=0
        ),
        SubMaterialQAStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.RETRIEVE_ANSWER.value,
            execute_result_code=0,
            execute_result_msg=(
                "未找到依据，已转人工" if material_qa.is_handoff
                else f"作答成功（置信度 {material_qa.confidence:.2f}）"
            ),
            execute_exceptions=[]
        )]
    }
