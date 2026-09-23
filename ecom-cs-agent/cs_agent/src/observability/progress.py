"""
链路进度事件。

节点内部调用 `emit()` 上报进度；脱离运行时（单测、直接调用节点函数）会自动
退化成普通日志，不会抛异常。

`quickstart.py` 的终端输出与 FastAPI 的 SSE 推送消费的是**同一套事件**，
所以 demo 里看到的进度就是线上推送的进度，不存在「演示专用代码」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from utils.agent_logger import get_logger

logger = get_logger("progress")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 阶段名 -> 中文标签（终端与前端共用，保证叫法一致）
STAGE_LABELS: dict[str, str] = {
    "load_data": "加载业务数据",
    "intent": "意图识别",
    "retrieve_start": "检索知识库",
    "retrieve_done": "检索完成",
    "rerank_done": "重排完成",
    "fanout": "并发调用业务接口",
    "normalize": "字段归一",
    "policy_match": "售后政策匹配",
    "llm_call": "调用大模型",
    "hitl_wait": "等待人工确认",
    "idempotent_replay": "命中幂等",
    "refund_submitted": "退款已提交",
    "handoff": "转人工",
    "report_built": "周报生成完成",
    "node_start": "开始",
    "node_done": "完成",
}


def emit(stage: str, detail: str = "", **data: Any) -> None:
    """
    上报一条进度事件。

    运行上下文内 -> 通过 LangGraph 自定义流通道发出（SSE / demo 可消费）
    运行上下文外 -> 退化为 debug 日志
    """
    payload = {
        "stage": stage,
        "label": STAGE_LABELS.get(stage, stage),
        "detail": detail,
        "ts": _now(),
        **data,
    }

    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer(payload)
    except Exception:
        # 脱离图运行时属于正常情况（单测直接调节点函数）
        logger.debug(f"[{payload['stage']}] {detail or payload.get('label')}")


def node_done(node_name: str, message: str = "", **data: Any) -> None:
    """节点完成事件的便捷封装。"""
    emit("node_done", detail=f"{node_name}: {message}" if message else node_name,
         node=node_name, message=message, **data)
