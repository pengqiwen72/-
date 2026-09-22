"""
工作流编排层:LangGraph 主图

架构说明:
- 统一调度层(route_intent)负责意图识别与任务分发
- 四条业务链路:智能问答 / 订单查询 / 售后处理 / 数据周报
- 链路间通过共享 State 传递上下文与中间结果
- 高风险操作经 HITL(interrupt)人工确认后执行
- 输出与运行记录统一汇总到 task_result / node_result
"""
import time
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.knowledge import CONFIDENCE_THRESHOLD, retrieve
from app.llm import INTENT_KEYWORDS, INTENT_PRIORITY, LLMClient
from app.models import Intent, NodeResult, SourceDoc, TaskResult
from app.state import AgentState
from app.tools import (
    extract_order_id,
    get_logistics_api,
    get_order_api,
    get_stock_api,
    idempotency_store,
    match_after_sale_policy,
    normalize_order,
)
from app.weekly import build_weekly_stats

llm = LLMClient()


# ------------------------------------------------------------------ 工具函数
def _elapsed(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# ------------------------------------------------------------------ 调度节点
def classify_intent(query: str) -> tuple[Intent, str]:
    """
    双层意图识别:
    1. 枚举精确匹配(高频意图,零误判、不消耗模型调用)
       - 多意图同时命中时:先比命中关键词数量,再按业务优先级裁决(售后 > 订单查询 > 周报 > 商品咨询)
    2. 未命中 -> 语义识别兜底
    """
    hits: dict[Intent, list[str]] = {}
    for intent, words in INTENT_KEYWORDS.items():
        matched = [w for w in words if w in query]
        if matched:
            hits[intent] = matched

    if hits:
        best = max(hits, key=lambda i: (len(hits[i]), -INTENT_PRIORITY.index(i)))
        return best, f"枚举精确匹配命中关键词 {hits[best]}"

    return llm.semantic_intent(query)


def route_intent(state: AgentState) -> dict:
    t0 = time.perf_counter()
    intent, reason = classify_intent(state["query"])
    return {
        "intent": intent.value,
        "route_reason": reason,
        "node_result": [NodeResult(node="route_intent", detail=f"意图={intent.value} | 依据={reason}")],
    }


def reset_state(state: AgentState) -> dict:
    """
    新一轮请求:清空上一轮的中间结果与执行记录
    —— 借助自定义 reducer 的「update 为空则置空」语义,避免多轮会话中旧结果污染本轮输出
    """
    return {
        "node_result": [],
        "task_result": None,
        "need_human": False,
        "decision": None,
        "human_decision": None,
        "order": None,
        "context": [],
        "stats": None,
    }


# ------------------------------------------------------- 链路 1:智能问答
def qa_chain(state: AgentState) -> dict:
    t0 = time.perf_counter()
    query = state["query"]
    docs = retrieve(query, top_k=3)

    # 质量判定:无命中 / 置信度不足 -> 不强行作答,转人工兜底
    if not docs:
        return {
            "task_result": TaskResult(
                intent=state["intent"], status="fallback",
                content="知识库未覆盖该问题,已自动转人工客服跟进(工单已创建)。",
                latency_ms=_elapsed(t0),
            ),
            "node_result": [NodeResult(node="qa_chain", detail="检索无命中(低于阈值) -> 转人工兜底")],
        }

    out = llm.answer_with_context(query, [d.model_dump() for d in docs])
    if out.confidence and out.confidence < CONFIDENCE_THRESHOLD:
        return {
            "task_result": TaskResult(
                intent=state["intent"], status="fallback",
                content="现有资料不足以确认该问题,已转人工客服核实后回复。",
                sources=docs, latency_ms=_elapsed(t0),
            ),
            "node_result": [NodeResult(node="qa_chain", detail=f"模型置信度 {out.confidence} 低于阈值 -> 转人工")],
        }

    return {
        "context": [d.model_dump() for d in docs],
        "task_result": TaskResult(
            intent=state["intent"], status="success", content=out.answer,
            sources=docs, latency_ms=_elapsed(t0),
        ),
        "node_result": [NodeResult(
            node="qa_chain",
            detail=f"RAG 检索命中 {len(docs)} 篇,最高相似度 {docs[0].score},回答携带出处",
        )],
    }


# ------------------------------------------------------- 链路 2:订单查询
def order_chain(state: AgentState) -> dict:
    t0 = time.perf_counter()
    order_id = extract_order_id(state["query"])
    if not order_id:
        return {
            "task_result": TaskResult(
                intent=state["intent"], status="fallback",
                content="未识别到订单号,请提供订单号(如 SO20260618001)后我再帮您查询。",
                latency_ms=_elapsed(t0),
            ),
            "node_result": [NodeResult(node="order_chain", detail="未抽取到订单号 -> 引导用户补充")],
        }

    raw = get_order_api(order_id)
    if not raw:
        return {
            "task_result": TaskResult(
                intent=state["intent"], status="fallback",
                content=f"订单 {order_id} 未查询到记录,已转人工客服核实。",
                latency_ms=_elapsed(t0),
            ),
            "node_result": [NodeResult(node="order_chain", detail="订单接口返回空 -> 转人工")],
        }

    # 多路 API 编排:订单 + 物流 + 库存
    logistics = get_logistics_api(order_id)
    stock = get_stock_api(raw["sku_code"])
    order = normalize_order(raw, logistics, stock)      # 字段归一 -> 统一契约
    content = llm.paraphrase_order(order)               # 模型只转述,不生成事实

    return {
        "order": order,
        "task_result": TaskResult(
            intent=state["intent"], status="success", content=content, latency_ms=_elapsed(t0),
        ),
        "node_result": [NodeResult(
            node="order_chain",
            detail=f"编排 3 路 API(订单/物流/库存),字段归一后由模型转述;库存 {stock['available']} 件",
        )],
    }


# ------------------------------------------------------- 链路 3:售后处理
def after_sale_chain(state: AgentState) -> dict:
    t0 = time.perf_counter()
    order_id = extract_order_id(state["query"])

    # 订单来源:优先用本轮问题中抽取到的订单号,避免多轮会话中误用上一轮残留订单
    if order_id:
        raw = get_order_api(order_id)
        order = normalize_order(raw, get_logistics_api(order_id), {}) if raw else None
    else:
        order = state.get("order")

    if order is None:
        return {
            "task_result": TaskResult(
                intent=state["intent"], status="fallback",
                content="请提供需要处理的订单号,我帮您核对售后政策。",
                latency_ms=_elapsed(t0),
            ),
            "node_result": [NodeResult(node="after_sale_chain", detail="缺少订单信息 -> 引导补充")],
        }

    decision = match_after_sale_policy(order, state["query"])   # 政策条件匹配

    # 高风险操作:不直接执行,标记待人工确认
    if decision.need_human:
        return {
            "decision": decision,
            "need_human": True,
            "node_result": [NodeResult(node="after_sale_chain", detail=f"政策匹配:{decision.reason} -> 转人工审批")],
        }

    # 低风险:自动执行(幂等)
    exec_result = idempotency_store.execute(decision.order_id, decision.action, decision.amount)
    return {
        "decision": decision,
        "need_human": False,
        "task_result": TaskResult(
            intent=state["intent"], status="success",
            content=f"订单 {decision.order_id} {decision.reason};{exec_result['message']},工单号 {exec_result['ticket']}。",
            latency_ms=_elapsed(t0),
        ),
        "node_result": [NodeResult(node="after_sale_chain", detail=f"低风险自动执行 {decision.action}(幂等键 {decision.order_id}:{decision.action})")],
    }


# --------------------------------------------------- 人工确认节点(HITL)
def human_approval(state: AgentState) -> dict:
    """
    LangGraph interrupt:高风险售后操作在此挂起,
    由主管审批后通过 Command(resume=...) 恢复执行
    """
    t0 = time.perf_counter()
    d = state["decision"]

    human = interrupt({
        "type": "after_sale_approval",
        "order_id": d.order_id,
        "action": d.action,
        "amount": d.amount,
        "risk_level": d.risk_level,
        "reason": d.reason,
        "question": f"订单 {d.order_id} 申请 {d.action} {d.amount} 元,是否批准执行?",
    })

    approved = str(human).strip().lower() in {"approve", "yes", "同意", "通过", "批准"}

    if not approved:
        return {
            "human_decision": "reject",
            "task_result": TaskResult(
                intent=state["intent"], status="rejected",
                content=f"订单 {d.order_id} 的 {d.action} 申请已被驳回,已通知客服向客户说明原因。",
                latency_ms=_elapsed(t0),
            ),
            "node_result": [NodeResult(node="human_approval", detail=f"人工审批结果:驳回({human})")],
        }

    exec_result = idempotency_store.execute(d.order_id, d.action, d.amount)
    replay = "(幂等命中,返回首次执行结果)" if exec_result["idempotent_replay"] else ""
    return {
        "human_decision": "approve",
        "task_result": TaskResult(
            intent=state["intent"], status="success",
            content=f"审批通过,订单 {d.order_id} 的{d.action}已执行:{exec_result['message']},工单号 {exec_result['ticket']}{replay}。",
            latency_ms=_elapsed(t0),
        ),
        "node_result": [NodeResult(
            node="human_approval",
            detail=f"人工审批结果:通过;幂等键 {d.order_id}:{d.action}{' 命中重放' if exec_result['idempotent_replay'] else ''}",
        )],
    }


# ------------------------------------------------------- 链路 4:数据周报
def weekly_chain(state: AgentState) -> dict:
    t0 = time.perf_counter()
    stats = build_weekly_stats()                 # 数值全部由代码计算
    summary = llm.summarize_weekly(stats)        # 模型只写摘要
    return {
        "stats": stats,
        "task_result": TaskResult(
            intent=state["intent"], status="success", content=summary, latency_ms=_elapsed(t0),
        ),
        "node_result": [NodeResult(
            node="weekly_chain",
            detail=f"跨平台数据清洗汇总完成;GMV {stats.gmv:.0f} 元(环比 {stats.gmv_wow:+.1f}%),模型仅生成摘要",
        )],
    }


# ------------------------------------------------------------------ 汇总节点
def finalize(state: AgentState) -> dict:
    result = state.get("task_result")
    if result is None:
        result = TaskResult(
            intent=state.get("intent", Intent.GENERAL.value), status="fallback",
            content="暂时无法处理该问题,已转人工客服协助。",
        )
    return {"task_result": result}


# ------------------------------------------------------------------ 图构建
def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("reset_state", reset_state)
    builder.add_node("route_intent", route_intent)
    builder.add_node("qa_chain", qa_chain)
    builder.add_node("order_chain", order_chain)
    builder.add_node("after_sale_chain", after_sale_chain)
    builder.add_node("human_approval", human_approval)
    builder.add_node("weekly_chain", weekly_chain)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "reset_state")
    builder.add_edge("reset_state", "route_intent")

    # 统一调度层按意图分发到四条业务链路
    builder.add_conditional_edges(
        "route_intent",
        lambda s: s["intent"],
        {
            Intent.PRODUCT_QA.value: "qa_chain",
            Intent.GENERAL.value: "qa_chain",          # 未匹配 -> 通用问答兜底
            Intent.ORDER_QUERY.value: "order_chain",
            Intent.AFTER_SALE.value: "after_sale_chain",
            Intent.WEEKLY_REPORT.value: "weekly_chain",
        },
    )

    # 售后链路:高风险 -> 人工确认;低风险 -> 直接汇总
    builder.add_conditional_edges(
        "after_sale_chain",
        lambda s: "human_approval" if s.get("need_human") else "finalize",
    )

    for node in ("qa_chain", "order_chain", "weekly_chain", "human_approval"):
        builder.add_edge(node, "finalize")
    builder.add_edge("finalize", END)

    # checkpointer:支撑多轮会话与 interrupt/resume
    return builder.compile(checkpointer=MemorySaver())


graph = build_graph()
