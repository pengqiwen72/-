"""
状态层:统一状态模型 + 自定义 reducer

设计要点:多源业务数据统一收敛到状态模型,通过自定义 reducer 实现节点结果的增量合并
"""
from typing import List, Optional
from typing_extensions import Annotated, TypedDict

from app.models import (
    AfterSaleDecision,
    NodeResult,
    OrderInfo,
    TaskResult,
    WeeklyStats,
)


def reducer_node_result(current: List[NodeResult], update: List[NodeResult]) -> List[NodeResult]:
    """
    自定义 reducer:
    - update 为空 -> 置空(用于新一轮请求重置执行记录)
    - 否则 -> 追加(current + update),保证多条业务链路的结果不被覆盖
    """
    if not update:
        return []
    return current + update


class AgentState(TypedDict, total=False):
    """统一状态模型:承载输入、中间结果与输出"""

    # ---- 输入 ----
    query: str
    user_id: str
    session_id: str

    # ---- 路由 ----
    intent: str
    route_reason: str

    # ---- 业务中间结果 ----
    context: List[dict]            # RAG 检索上下文
    order: Optional[OrderInfo]
    decision: Optional[AfterSaleDecision]
    stats: Optional[WeeklyStats]

    # ---- 人工确认(HITL) ----
    need_human: bool
    human_decision: Optional[str]  # approve / reject

    # ---- 输出 ----
    task_result: Optional[TaskResult]
    node_result: Annotated[List[NodeResult], reducer_node_result]
