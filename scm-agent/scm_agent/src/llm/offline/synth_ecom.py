"""
电商客服业务链路的离线合成器（商品咨询 / 订单查询 / 售后 / 周报）。

目前只有周报需要模型产出**结构化 JSON**，所以这里只注册了 `ReportNarrative`。
商品咨询走的是纯文本路径（`llm_rag_answer`），订单查询与售后是确定性装配，
都不经过离线合成器 —— 离线合成器的职责只有一个：**替模型产出 schema 合法的
结构化对象**，没有结构化产出的链路就不该在这里出现。

周报合成器的原则和 `synth_ops` 一致：**数据驱动**。
它把提示词里的指标数值原样取出来，重新组织成中文句子。返回固定常量在这儿尤其
危险：周报的数字每周都在变，写死的摘要会让「离线模式」当场露馅；更关键的是，
摘要里的数字必须能在事实单里找到，否则调用方的 `verify_no_new_numbers` 会把它
整段丢掉 —— 用真实数值拼句子是唯一能同时满足「像人话」和「不编造」的做法。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm.offline.prompt_view import PromptView
from llm.offline.registry import synthesizer
from models.scm_models import ReportNarrative
from utils.agent_logger import get_logger

logger = get_logger("synth_ecom")

#: 摘要里按这个顺序找指标。名字对不上就跳过，不让一个改名把整段摘要搞崩。
_SUMMARY_ORDER = ("成交订单量", "成交金额", "客单价", "退款单量", "退款率", "在途订单量")

#: 最多几条要点。提示词里也是 4 条，两处保持一致。
MAX_HIGHLIGHTS = 4


def _fmt(value: Any) -> str:
    """把指标值格式化成**不会引入新数字**的字符串。

    `:g` 是关键：`1700.0` 输出 `1700`，与事实单里 `1700.0` 归一化后的
    `1700` 一致。若用 `str(1700.0)` 会输出 `1700.0`（归一化后同样是 `1700`，
    也能过），但 `1e+03` 这种科学计数法就会真的引入一个事实单里没有的数字。
    """
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value or "")


def _by_name(metrics: List[dict]) -> Dict[str, dict]:
    return {str(m.get("name", "")): m for m in metrics if isinstance(m, dict)}


def _trend_phrase(metric: Optional[dict]) -> str:
    """引用 delta_pct 描述涨跌；上周无可比数据时坦率说明，不臆测。"""
    if not metric:
        return ""
    pct = metric.get("delta_pct")
    name = str(metric.get("name", ""))
    if pct is None:
        # 没有基数就不谈涨跌。这句由异常提示去解释（见 sub_weekly_report_node._anomalies），
        # 摘要里再写一遍「上周无可比数据」只会变成把同一件事说两次。
        return ""
    try:
        value = abs(float(pct))
    except (TypeError, ValueError):
        return ""
    if value == 0:
        return f"{name}与上周持平"
    direction = "上升" if float(pct) > 0 else "下降"
    return f"{name}较上周{direction} {_fmt(value)}%"


@synthesizer(ReportNarrative)
def synthesize_weekly_report(view: PromptView) -> dict:
    """离线周报摘要：把算好的指标数值重新组织成中文句子。

    数值全部来自提示词里的指标块，一个字都不自己算 —— 这正是线上提示词对
    模型的第 4 条要求，离线实现只是用「照抄 + 拼接」把它落实得更彻底。
    """
    block = view.find("metrics")
    if not block:
        logger.warning("周报提示词里没找到指标块，返回空叙述")
        return {"summary": "", "highlights": []}

    metrics = [m for m in (block.get("metrics") or []) if isinstance(m, dict)]
    anomalies = [a for a in (block.get("anomalies") or []) if isinstance(a, dict)]
    by_name = _by_name(metrics)

    start = str(block.get("period_start") or "")
    end = str(block.get("period_end") or "")

    sentences: List[str] = []
    period = f"本周（{start} ~ {end}）" if start and end else "本周"

    head: List[str] = []
    if "成交订单量" in by_name:
        head.append(f"成交 {_fmt(by_name['成交订单量'].get('current', 0))} 单")
    if "成交金额" in by_name:
        head.append(f"成交金额 {_fmt(by_name['成交金额'].get('current', 0))} 元")
    if "客单价" in by_name:
        head.append(f"客单价 {_fmt(by_name['客单价'].get('current', 0))} 元")
    if head:
        sentences.append(f"{period}，" + "，".join(head))

    # 趋势只引一个指标，避免摘要变成念表：成交金额优先，没有就退回订单量
    trend = _trend_phrase(by_name.get("成交金额") or by_name.get("成交订单量"))
    if trend:
        sentences.append(trend)

    tail: List[str] = []
    if "退款单量" in by_name:
        tail.append(f"退款 {_fmt(by_name['退款单量'].get('current', 0))} 单")
    if "退款率" in by_name:
        tail.append(f"退款率 {_fmt(by_name['退款率'].get('current', 0))}%")
    if "在途订单量" in by_name:
        tail.append(f"在途 {_fmt(by_name['在途订单量'].get('current', 0))} 单")
    if tail:
        sentences.append("，".join(tail))

    # ⚠️ 这里刻意**不写「有 N 项预警」**：N 是个新数字，事实单里没有它，
    # 一旦换了批数据就会让整段摘要过不了 verify_no_new_numbers 而被丢弃。
    # 引用异常条目自己的 detail 则天然安全 —— 它已经躺在事实单里了。
    warn = [a for a in anomalies if a.get("level") == "warn"]
    if warn:
        detail = str(warn[0].get("detail") or "").strip().rstrip("。")
        sentences.append(f"其中{detail}，需要重点关注" if detail else "本周存在需要重点关注的异常")

    summary = "；".join(sentences) + "。" if sentences else ""

    return {"summary": summary, "highlights": _highlights(anomalies, by_name)}


def _highlights(anomalies: List[dict], by_name: Dict[str, dict]) -> List[str]:
    """要点：异常条目优先，不够再补波动最大的指标。

    异常条目的文案由确定性代码生成、且已经出现在事实单里，所以直接引用它
    既是最有信息量的，也是唯一保证不引入新数字的写法。
    """
    out: List[str] = []

    for level in ("warn", "info"):
        for item in anomalies:
            if item.get("level") != level:
                continue
            detail = str(item.get("detail") or "").strip()
            if detail and detail not in out:
                out.append(detail)
            if len(out) >= MAX_HIGHLIGHTS:
                return out

    # 异常不够时，用环比波动最大的指标补齐
    movers = [
        m
        for m in by_name.values()
        if m.get("delta_pct") is not None and str(m.get("name", "")) in _SUMMARY_ORDER
    ]
    movers.sort(key=lambda m: abs(float(m.get("delta_pct") or 0)), reverse=True)

    for metric in movers:
        if len(out) >= MAX_HIGHLIGHTS:
            break
        phrase = _trend_phrase(metric)
        if phrase and phrase not in out:
            out.append(phrase)

    return out
