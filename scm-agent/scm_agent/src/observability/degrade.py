"""
双通道问答的分级降级状态。

四级语义
--------
    L0  双通道正常（文档 + 图谱）
    L1  图谱不可用 -> 纯文档作答，页脚注明「本次未启用图谱关联分析」
    L2  文档不可用 -> 只用图谱事实，页脚注明
    L3  双通道皆不可用 -> 只剩转人工

**这是「通道可用性」的分级，不是「结论质量」的分级。** 置信度不足的转人工
（语料里没有依据）不改变通道可用性：qa_miss 场景是 L0 通道 + handoff 结局。
两者正交，最终都会汇入「转人工」这条兜底，但原因不同、话术不同 —— 把
「通道挂了」和「资料里没写」混成一个级别，排查时就看不出该修谁。

为什么是进程级状态
------------------
级别要在**检索层**（`rag/retriever.py`、`kg/query.py`）标记、在**作答层**
（`rag/index.py::ask`）读出来拼页脚，中间隔着几层调用，逐层传参会把参数表
撑得很难看。代价：多请求并发时状态互相覆盖 —— 本 demo 是单进程单会话演示，
并发窗口内出现的覆盖由 SSE 时间线上的 `degrade` 事件兜底（那条是请求级的）。
真实系统里这层状态应当随请求上下文传递，这里用模块级变量是演示规模下的取舍。

请求边界
--------
`ask()` 在入口处 `reset()` 一次，页脚只反映**本次问答**期间的通道状况，
不会把上一轮的降级带进下一轮。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List

from observability.progress import emit
from utils.agent_logger import get_logger

logger = get_logger("degrade")


class DegradeLevel(IntEnum):
    L0 = 0   # 双通道正常
    L1 = 1   # 图谱不可用
    L2 = 2   # 文档不可用
    L3 = 3   # 双通道皆不可用（= L1 与 L2 同时成立时的提升）


#: 页脚文案：只对「通道缺口」说话 —— 用户能看到的是「这次少用了什么」，
#: 而不是一个编号。L3 不单列文案：那时答案本身已转人工，页脚再加一句是冗余。
_FOOTERS: Dict[DegradeLevel, str] = {
    DegradeLevel.L1: "（本次未启用图谱关联分析）",
    DegradeLevel.L2: "（本次未引用制度文档，仅使用图谱事实）",
}

_level: DegradeLevel = DegradeLevel.L0
_reasons: List[str] = []


def reset() -> None:
    global _level, _reasons
    _level = DegradeLevel.L0
    _reasons = []


def mark(level: DegradeLevel, reason: str = "") -> None:
    """标记一次降级。级别只升不降；L1 与 L2 同时出现时提升为 L3。"""
    global _level
    if _level != DegradeLevel.L3:
        if {_level, level} == {DegradeLevel.L1, DegradeLevel.L2}:
            _level = DegradeLevel.L3
        elif level > _level:
            _level = level

    if reason:
        _reasons.append(reason)
    logger.warning(f"降级 {level.name}：{reason or '未说明原因'}")
    # 请求级信号：SSE 时间线上标出「这一次问答里发生了什么降级」
    emit("degrade", detail=f"{level.name}：{reason}" if reason else level.name,
         level=level.name, level_value=int(level))


def current() -> DegradeLevel:
    return _level


def reasons() -> List[str]:
    return list(_reasons)


def footer() -> str:
    """当前级别的页脚文案；L0 返回空串。"""
    return _FOOTERS.get(_level, "")
