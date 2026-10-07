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
    # 周报链路内部的分段（取数 → 算数 → 说话），终端与工作台都按这些标签显示
    "report_window": "周报统计周期",
    "report_collect": "周报取数",
    "report_metric": "指标计算",
    "report_anomaly": "异常识别",
    "report_narrative": "生成摘要",
    "report_verify_failed": "摘要数字校验未通过",
    # 链路自身的业务步骤（由 NODE_EVENTS 兜底生成，见下）
    "result_confirm": "结果确认",
    "approval_pending": "已推送审批通知",
    "tag_generate": "生成标签建议",
    "tag_update": "更新客户标签",
    "profile_generate": "生成画像建议",
    "profile_update": "更新客户画像",
    "schedule_generate": "生成跟进任务",
    "schedule_create": "创建跟进任务",
    "chat_generate": "生成话术建议",
    "kf_chat_generate": "生成客服话术建议",
    "talk_reply": "闲聊兜底回复",
    "node_start": "开始",
    "node_done": "完成",
}


# 节点名 -> (stage, detail)：给「自己不 emit 的节点」兜底一条进度事件。
#
# 为什么需要这张表
# ----------------
# `emit()` 要靠节点作者手写，写了才有进度。事实是 10 条链路里只有 4 条写了
# （售后 / 订单 / 商品 / 周报）—— 标签、画像、跟进任务、话术建议、闲聊那几条
# 在工作台里的时间线是**空的**：点下去什么都不显示，然后审批卡片突然弹出来。
# 只看「有没有事件」的冒烟测试发现不了这种空档。
#
# 与其在 6 个子图里逐个补 `emit()`（漏一个还是空的），不如在 runner 消费 updates
# 通道时兜底生成：节点跑过就一定看得见，以后新增节点只要在这里登记一行。
# 节点自己 emit 过的不会再补 —— 判断在 `DemoRunner._iter`，否则那 4 条链路
# 每条都会变成「同样的话说两遍」。
#
# 只登记**业务步骤**：`cleanup_state`、`*_welcome_node` 这类内部节点不登记，
# 未登记的节点不产生事件。父图上代表子图的那几个节点名（`tag_suggestion` 等）
# 也不登记 —— 子图内部的节点已经把过程讲清楚了，再补一条是重复。
NODE_EVENTS: dict[str, tuple[str, str]] = {
    # 主图 · 数据装载：7 个节点拆开显示，让「统一状态模型汇多源数据」看得见
    "load_welcome_message_node":  ("load_data", "欢迎语"),
    "load_employee_info_node":    ("load_data", "坐席信息"),
    "load_tag_setting_node":      ("load_data", "标签体系"),
    "load_customer_info_node":    ("load_data", "客户信息与画像"),
    "load_chat_history_node":     ("load_data", "会话记录"),
    "load_kf_chat_history_node":  ("load_data", "客服会话"),
    "load_order_history_node":    ("load_data", "订单记录"),
    # 主图 · 路由与收尾
    "intent_detection":    ("intent", "枚举精确匹配 → 语义识别 → 兜底"),
    "task_result_confirm": ("result_confirm", ""),
    # 标签建议
    "tag_generate_node":        ("tag_generate", ""),
    "tag_notify_feedback_node": ("approval_pending", "标签建议"),
    "tag_update_node":          ("tag_update", ""),
    # 客户画像
    "profile_suggest_node":         ("profile_generate", ""),
    "profile_notify_feedback_node": ("approval_pending", "画像建议"),
    "profile_update_node":          ("profile_update", ""),
    # 跟进任务
    "schedule_generate_node": ("schedule_generate", ""),
    "schedule_create_node":   ("schedule_create", ""),
    # 话术建议
    "chat_generate_node":    ("chat_generate", ""),
    "kf_chat_generate_node": ("kf_chat_generate", ""),
    # 闲聊兜底
    "talk_reply_node": ("talk_reply", ""),
}


def node_event(node_name: str) -> Optional[dict]:
    """按节点名造一条进度事件；未登记的节点返回 None。

    形状与 `emit()` 发出的完全一致（`stage` / `label` / `detail` / `ts`），
    这样消费端（终端与工作台）不需要区分两种来源。`node` 是节点原名，
    `node_done()` 也会带上它 —— 所以它标的是「哪个节点产生的」，不是「谁兜底的」。
    """
    spec = NODE_EVENTS.get(node_name)
    if not spec:
        return None
    stage, detail = spec
    return {
        "stage": stage,
        "label": STAGE_LABELS.get(stage, stage),
        "detail": detail,
        "ts": _now(),
        "node": node_name,
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
    """节点完成事件的便捷封装。

    detail 里**不再拼节点名**：名字已经在 `node` 字段里，中文时间线上再出现一次
    `report_collect: 本周 6 笔` 只会让界面看起来像调试日志。
    """
    emit("node_done", detail=message, node=node_name, message=message, **data)
