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
    "channel_route": "通道路由",
    "retrieve_start": "检索知识库",
    "retrieve_done": "检索完成",
    "graph_link": "图谱实体链接",
    "graph_query": "图谱多跳查询",
    "rrf_fuse": "融合召回",
    "rerank_done": "重排完成",
    "degrade": "降级",
    "policy_match": "采购策略匹配",
    "quality_check": "质检判定",
    "hitl_wait": "等待人工确认",
    "idempotent_replay": "命中幂等",
    "purchase_submitted": "采购订单已下达",
    "receipt_submitted": "收货已登记",
    "stock_aggregate": "库存聚合",
    "supplier_score": "供应商评分",
    "handoff": "转人工",
    # 链路自身的业务步骤（由 NODE_EVENTS 兜底生成，见下）
    "talk_reply": "闲聊兜底回复",
    "result_confirm": "结果确认",
    "node_done": "完成",
}


# 节点名 -> (stage, detail)：给「自己不 emit 的节点」兜底一条进度事件。
#
# 为什么需要这张表
# ----------------
# `emit()` 要靠节点作者手写，写了才有进度。写漏的节点在工作台里的时间线是
# **空的**：点下去什么都不显示，然后审批卡片突然弹出来。只看「有没有事件」的
# 冒烟测试发现不了这种空档。
#
# 与其在每个子图里逐个盯 `emit()`（漏一个还是空的），不如在 runner 消费 updates
# 通道时兜底生成：节点跑过就一定看得见，以后新增节点只要在这里登记一行。
# 节点自己 emit 过的不会再补 —— 判断在 `DemoRunner._iter`，否则写全了的链路
# 会变成「同样的话说两遍」。
#
# 只登记**业务步骤**：`cleanup_state`、`*_welcome_node` 这类内部节点不登记，
# 未登记的节点不产生事件。父图上代表子图的那几个节点名（`purchase_request` 等）
# 也不登记 —— 子图内部的节点已经把过程讲清楚了，再补一条是重复。
NODE_EVENTS: dict[str, tuple[str, str]] = {
    # 主图 · 数据装载：7 个节点拆开显示，让「统一状态模型汇多源数据」看得见
    "load_welcome_message_node":  ("load_data", "欢迎语"),
    "load_operator_info_node":    ("load_data", "操作员信息"),
    "load_materials_node":        ("load_data", "物料主数据"),
    "load_suppliers_node":        ("load_data", "供应商主数据"),
    "load_warehouses_node":       ("load_data", "仓库主数据"),
    "load_stock_node":            ("load_data", "库存快照"),
    "load_purchase_orders_node":  ("load_data", "采购订单"),
    # 主图 · 路由与收尾
    "intent_detection":    ("intent", "枚举精确匹配 → 语义识别 → 兜底"),
    "task_result_confirm": ("result_confirm", ""),
    # 物料与制度问答（节点自己 emit 检索事件，这里是漏 emit 时的兜底）
    "material_qa_retrieve_node": ("retrieve_start", ""),
    # 采购申请
    "purchase_request_match_node":    ("policy_match", ""),
    "purchase_request_approval_node": ("hitl_wait", ""),
    "purchase_request_execute_node":  ("purchase_submitted", ""),
    # 收货验收
    "goods_receipt_match_node":    ("quality_check", ""),
    "goods_receipt_approval_node": ("hitl_wait", ""),
    "goods_receipt_execute_node":  ("receipt_submitted", ""),
    # 库存管理
    "inventory_query_node": ("stock_aggregate", ""),
    # 供应商管理
    "supplier_resolve_node": ("supplier_score", "解析物料与报价"),
    "supplier_report_node":  ("supplier_score", "加权评分排名"),
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
