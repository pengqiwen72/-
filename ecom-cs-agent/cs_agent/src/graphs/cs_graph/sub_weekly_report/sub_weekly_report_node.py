"""
数据周报子图节点。

    collect（取数）→ aggregate（算数）→ narrate（说话）

三个动作分成三个节点，不是为了好看，而是因为它们的**可信度来源不同**：

    collect    —— 依赖上游订单中台，可能失败、可能缺数据，所以允许降级；
    aggregate  —— 纯函数，同样的输入必然得到同样的输出，**不需要模型也不需要网络**；
    narrate    —— 唯一接触模型的地方，也是唯一可能说错话的地方。

分开之后，「模型只负责说话」这条约束就从一句口号变成了图结构：narrate 节点
拿到的是算好的数字，它没有任何途径去改它们 —— 它只能影响 `summary` 和
`highlights` 这两个字符串字段。而且这两个字段还要再过一遍
`verify_no_new_numbers`：文字里出现的每个数字都必须能在指标表里找到，
找不到就把整段文字丢掉，换成确定性拼装的那一版。

关于异常提示为什么不用模型
--------------------------
「模型只生成摘要与异常提示」听着合理，但异常的本质是**阈值判断**：
退款率超过 10%、成交金额环比跌破 20%、在途超过 5 天。阈值判断是确定性的，
交给模型只会引入不确定性——它可能漏报一条明明踩线的指标，而漏报的代价
是坐席错过了本该处理的异常。所以阈值由代码判、理由由代码写，模型只在
摘要里引用这些已判定的事实。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig

from graphs.cs_graph.sub_weekly_report.sub_weekly_report_state import (
    SubWeeklyReportState,
    SubWeeklyReportStateField as F,
)
from integrations.factory import get_order_port
from integrations.normalize import normalize_order
from integrations.policy import days_since
from integrations.ports import IntegrationError
from llm.llm_weekly_report import build_report_facts, llm_weekly_report
from models.cs_models import (
    NodeResult,
    ReportAnomaly,
    ReportMetric,
    TaskResult,
    WeeklyReport,
)
from observability.progress import emit, node_done
from rag.answer import verify_no_new_numbers
from utils.agent_logger import get_logger

logger = get_logger("sub_weekly_report_node")


class NodeName(str, Enum):
    ''' 数据周报子图节点名 '''
    COLLECT   = "report_collect"
    AGGREGATE = "report_aggregate"
    NARRATE   = "report_narrate"


#: 统计窗口长度（天）。本周 = 今天往前 7 天（含今天），上周 = 再往前 7 天。
WINDOW_DAYS = 7

#: 计入「成交」的状态码。100 待付款还没成交，500 已取消更不算 ——
#: 把这两类算进 GMV 是最常见的报表口径错误，也是最容易在面试里被追问的一处。
_DEAL_STATUS = {"200", "300", "400", "600", "700"}
#: 退款相关状态
_REFUND_STATUS = {"600", "700"}
#: 在途：付了钱、客户还没收到货
_IN_TRANSIT_STATUS = {"200", "300"}

#: 异常阈值。写在模块级常量里而不是散在判断里，是为了让「口径」可以被 review：
#: 运营改口径时改的是这一处，不是去代码里找那个 `10`。
REFUND_RATE_WARN = 10.0     # 退款率警戒线（%）
REVENUE_DROP_WARN = -20.0   # 成交金额环比跌幅警戒线（%）
TRANSIT_DAYS_WARN = 5       # 在途超过多少天算超时


# ============================================================ 窗口与取数

def _today() -> date:
    """今天。可用 `CS_REPORT_TODAY=yyyy-MM-dd` 固定，便于测试与复现。

    报表类功能没有这个开关就没法写断言——「上周」每天都在变，
    测试只能写成「跑两次结果一致」，那等于什么都没测。
    """
    fixed = (os.getenv("CS_REPORT_TODAY") or "").strip()
    if fixed:
        try:
            return datetime.strptime(fixed, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"CS_REPORT_TODAY 无法解析：{fixed!r}，改用系统日期")
    return date.today()


def _window(today: Optional[date] = None) -> Tuple[str, str, str, str]:
    """返回 (本周起, 本周止, 上周起, 上周止)，都是 `yyyy-MM-dd`。

    用**滚动 7 天**而不是自然周（周一~周日）：一是周报不必等到周一才能看，
    二是自然周会让「今天跑」和「明天跑」命中不同的周期，演示时结果飘忽。
    滚动窗口的代价是同事对不上日历，所以周期起止会明确打印在周报标题上。
    """
    today = today or _today()
    cur_end = today
    cur_start = today - timedelta(days=WINDOW_DAYS - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=WINDOW_DAYS - 1)

    fmt = lambda d: d.strftime("%Y-%m-%d")  # noqa: E731 —— 局部小工具，不值得单开函数
    return fmt(cur_start), fmt(cur_end), fmt(prev_start), fmt(prev_end)


def _fetch(start: str, end: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """按区间取订单并归一。返回 (归一行, 降级说明)。"""
    notes: List[str] = []

    try:
        raw_rows = get_order_port().list_orders_by_range(start, end)
    except IntegrationError as e:
        # 订单中台挂了，整张报表确实出不来——但要说清楚是「没数据」还是「取不到数」，
        # 否则运营会以为这周真的零成交。
        logger.warning(f"区间 {start}~{end} 取单失败：{e}")
        return [], [f"订单中台不可用：{e}"]

    rows: List[Dict[str, Any]] = []
    skipped = 0
    for raw in raw_rows:
        try:
            row = normalize_order(raw)
        except Exception as e:  # noqa: BLE001 —— 单条脏数据不该让整张报表失败
            skipped += 1
            logger.warning(f"跳过无法归一的订单：{e}")
            continue
        if row["status_code"]:
            rows.append(row)
        else:
            # 状态取不到就没法归类到任一指标，算进去反而会让口径变得不可解释
            skipped += 1
            logger.warning(f"订单 {row.get('order_id')} 状态无法识别，不计入统计")

    if skipped:
        notes.append(f"{skipped} 笔订单数据异常已跳过")
    return rows, notes


# ============================================================ 指标计算

def _raw_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Tuple[float, str]]:
    """把一批订单折成一个 指标名 -> (值, 单位) 的表。

    这是整条链路上唯一做算术的地方，也是唯一需要写测试的地方 ——
    它是纯函数，给定输入必然得到同样的输出。
    """
    deal = [r for r in rows if r["status_code"] in _DEAL_STATUS]
    refund = [r for r in rows if r["status_code"] in _REFUND_STATUS]
    transit = [r for r in rows if r["status_code"] in _IN_TRANSIT_STATUS]

    revenue = sum(r["pay_amount"] for r in deal)
    refund_amount = sum(r["pay_amount"] for r in refund)

    return {
        "成交订单量": (len(deal), "单"),
        "成交金额": (round(revenue, 2), "元"),
        # 客单价的分母用成交单量而不是全部订单量：口径必须与分子一致，
        # 否则「成交金额 / 含未付款的订单数」会算出一个没人能解释的数
        "客单价": (round(revenue / len(deal), 2) if deal else 0.0, "元"),
        "退款单量": (len(refund), "单"),
        "退款金额": (round(refund_amount, 2), "元"),
        "退款率": (round(len(refund) / len(deal) * 100, 1) if deal else 0.0, "%"),
        "在途订单量": (len(transit), "单"),
    }


def _pct(current: float, previous: float) -> Optional[float]:
    """环比百分比。上周为 0 时返回 None —— 不是 0%，也不是 +∞%。"""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _build_metrics(cur: Dict[str, Tuple[float, str]],
                   prev: Dict[str, Tuple[float, str]]) -> List[ReportMetric]:
    out: List[ReportMetric] = []
    for name, (value, unit) in cur.items():
        before = prev.get(name, (0.0, unit))[0]
        out.append(ReportMetric(
            name=name,
            current=float(value),
            previous=float(before),
            delta=round(float(value) - float(before), 2),
            delta_pct=_pct(float(value), float(before)),
            unit=unit,
        ))
    return out


def _anomalies(metrics: List[ReportMetric], rows: List[Dict[str, Any]]) -> List[ReportAnomaly]:
    """规则化异常判定。每条异常都必须能指到具体指标与阈值。"""
    by_name = {m.name: m for m in metrics}
    out: List[ReportAnomaly] = []

    rate = by_name.get("退款率")
    if rate and rate.current > REFUND_RATE_WARN:
        out.append(ReportAnomaly(
            metric="退款率", level="warn",
            detail=f"退款率 {rate.current:g}%，超过 {REFUND_RATE_WARN:g}% 的警戒线",
        ))

    gmv = by_name.get("成交金额")
    if gmv and gmv.delta_pct is not None and gmv.delta_pct < REVENUE_DROP_WARN:
        out.append(ReportAnomaly(
            metric="成交金额", level="warn",
            detail=f"成交金额环比下降 {abs(gmv.delta_pct):g}%，跌幅超过 {abs(REVENUE_DROP_WARN):g}%",
        ))
    elif gmv and gmv.delta_pct is None:
        # 环比算不出来有两种原因，别混成一句「上周无数据」了事 ——
        # 一种是上周刚好零成交（这周好得很），一种是取数根本没取到（这是事故）。
        if gmv.current > 0:
            out.append(ReportAnomaly(
                metric="成交金额", level="info",
                detail="上周无可比成交数据，环比指标仅供参考",
            ))
        elif gmv.previous <= 0:
            out.append(ReportAnomaly(
                metric="成交金额", level="warn",
                detail="本周与上周均无成交订单，请先确认取数口径是否正常",
            ))

    transit_days = _max_transit_days(rows)
    if transit_days is not None and transit_days >= TRANSIT_DAYS_WARN:
        out.append(ReportAnomaly(
            metric="在途订单量", level="warn",
            detail=f"有在途订单已下单 {transit_days} 天仍未签收，超过 {TRANSIT_DAYS_WARN} 天的时效线",
        ))

    if not out:
        out.append(ReportAnomaly(metric="", level="info", detail="本周未发现异常指标"))

    return out


def _max_transit_days(rows: List[Dict[str, Any]]) -> Optional[int]:
    """在途订单里最久的一单已经下了多少天。没有在途单则返回 None。"""
    days: List[int] = []
    for row in rows:
        if row["status_code"] not in _IN_TRANSIT_STATUS or not row["created_at"]:
            continue
        try:
            value = days_since(row["created_at"])
        except Exception as e:  # noqa: BLE001 —— 时间格式异常不该让周报挂掉
            logger.warning(f"订单 {row.get('order_id')} 下单时间无法解析：{e}")
            continue
        if value is not None:
            days.append(value)
    return max(days) if days else None


def _table(metrics: List[ReportMetric]) -> str:
    """Markdown 表格。后台页面和终端都能直接渲染，不需要各自再拼一遍。"""
    lines = ["| 指标 | 本周 | 上周 | 环比 |", "| --- | ---: | ---: | ---: |"]
    for m in metrics:
        if m.delta_pct is None:
            trend = "—"
        else:
            arrow = "↑" if m.delta_pct > 0 else ("↓" if m.delta_pct < 0 else "→")
            trend = f"{arrow} {abs(m.delta_pct):g}%"
        lines.append(
            f"| {m.name} | {m.current:g}{m.unit} | {m.previous:g}{m.unit} | {trend} |"
        )
    return "\n".join(lines)


# ============================================================ 节点

def collect_node(state: SubWeeklyReportState, config: RunnableConfig):
    """定窗口、拉订单、归一。只有这一步会碰外部系统。"""
    logger.info("=== 数据周报：取数 ===")

    cur_start, cur_end, prev_start, prev_end = _window()
    emit("report_window", detail=f"统计周期 {cur_start} ~ {cur_end}（上周 {prev_start} ~ {prev_end}）")

    cur_rows, cur_notes = _fetch(cur_start, cur_end)
    prev_rows, prev_notes = _fetch(prev_start, prev_end)

    notes = cur_notes + prev_notes
    emit("report_collect",
         detail=f"本周命中 {len(cur_rows)} 笔订单，上周 {len(prev_rows)} 笔")
    # 不再补一条 node_done 报同样的两个数字 —— 见 progress.node_done 的说明

    return {
        F.ROWS_CURRENT.value: cur_rows,
        F.ROWS_PREVIOUS.value: prev_rows,
        F.WINDOW.value: [cur_start, cur_end, prev_start, prev_end],
        F.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.COLLECT.value,
            execute_result_code=0,
            execute_result_msg=f"本周 {len(cur_rows)} 笔订单，上周 {len(prev_rows)} 笔订单",
            execute_exceptions=notes
        )]
    }


def aggregate_node(state: SubWeeklyReportState, config: RunnableConfig):
    """算指标、判异常、出表格。**纯函数，不调模型。**"""
    logger.info("=== 数据周报：算数 ===")

    cur_rows = state.get(F.ROWS_CURRENT.value) or []
    prev_rows = state.get(F.ROWS_PREVIOUS.value) or []
    cur_start, cur_end, prev_start, prev_end = state.get(F.WINDOW.value) or _window()

    metrics = _build_metrics(_raw_metrics(cur_rows), _raw_metrics(prev_rows))
    anomalies = _anomalies(metrics, cur_rows)

    for m in metrics:
        emit("report_metric", detail=m.render())
    for a in anomalies:
        emit("report_anomaly", detail=a.render())

    report = WeeklyReport(
        period_start=cur_start,
        period_end=cur_end,
        metrics=metrics,
        anomalies=anomalies,
        table=_table(metrics),
        # 这里先把表格与异常定稿，摘要留空由 narrate 节点补。
        # 即便模型那一跳整个失败，运营拿到的也是一张完整的指标表。
        summary="",
        highlights=[],
    )

    node_done(NodeName.AGGREGATE.value, f"{len(metrics)} 项指标 / {len(anomalies)} 条异常")

    return {
        F.WEEKLY_REPORT: report,
        F.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.AGGREGATE.value,
            execute_result_code=0,
            execute_result_msg=f"完成 {len(metrics)} 项指标的同环比计算",
            execute_exceptions=[]
        )]
    }


def narrate_node(state: SubWeeklyReportState, config: RunnableConfig):
    """让模型把算好的数字说成人话，并**逐个数字校验**它的措辞。"""
    logger.info("=== 数据周报：生成摘要 ===")

    report = state.get(F.WEEKLY_REPORT)
    if report is None or not report.metrics:
        # aggregate 都没跑出来，没什么可说的
        return _publish(
            WeeklyReport(degraded=True),
            "指标未计算成功", "aggregate 节点没有产出指标", degraded=True
        )

    facts = build_report_facts(
        report.metrics, report.anomalies, report.period_start, report.period_end
    )

    narrative = llm_weekly_report(
        metrics=report.metrics,
        anomalies=report.anomalies,
        period_start=report.period_start,
        period_end=report.period_end,
        table=report.table,
        facts=facts,
    )

    text = "\n".join([narrative.summary] + list(narrative.highlights)).strip()
    ok, offending = verify_no_new_numbers(text, facts)

    if text and ok:
        report.summary = narrative.summary
        report.highlights = list(narrative.highlights)
        emit("report_narrative", detail=f"摘要已生成，数字校验通过（{len(text)} 字）")
        return _publish(report, "摘要生成完成，数字校验通过", "")

    # 走到这里说明模型编了数字。**不能只是记个日志**——周报是要发给运营看的，
    # 一个凭空出现的「增长 3 倍」会被当成事实写进决策里。
    fallback_summary, fallback_highlights = _fallback_narrative(report)
    report.summary = fallback_summary
    report.highlights = fallback_highlights
    report.degraded = True

    if not text:
        note = "模型未产出内容"
    else:
        note = f"模型输出含无依据数字 {'、'.join(offending[:5])}，已丢弃并回退为确定性转述"
    emit("report_verify_failed", detail=note)
    logger.warning(f"周报摘要未通过数字校验：{note}")

    return _publish(report, "摘要已生成（确定性转述）", note, degraded=True)


def _fallback_narrative(report: WeeklyReport) -> Tuple[str, List[str]]:
    """不依赖模型拼一版摘要。

    和 `llm/offline/synth_ecom.py` 里的离线合成器长得很像，但职责不同，所以没有合并：

      - 离线合成器要**冒充模型**，它只能看到提示词，所以必须从提示词里反解数据；
      - 这里是模型失败后的安全网，手上就有结构化对象，直接读字段更快也更准。

    合并它俩会让其中一方被迫绕远路（要么安全网去解析自己刚拼的提示词，
    要么离线合成器拿到不该有的对象引用），得不偿失。
    """
    by_name = {m.name: m for m in report.metrics}

    head: List[str] = []
    if "成交订单量" in by_name:
        head.append(f"成交 {by_name['成交订单量'].current:g} 单")
    if "成交金额" in by_name:
        head.append(f"成交金额 {by_name['成交金额'].current:g} 元")
    if "客单价" in by_name:
        head.append(f"客单价 {by_name['客单价'].current:g} 元")

    # 成交那一串用逗号连，退款/在途那一串用分号隔开——两段各自是完整的意思，
    # 全用逗号读起来会变成一口气念不完的长句
    parts: List[str] = []
    if head:
        parts.append(f"本周（{report.period_start} ~ {report.period_end}），" + "，".join(head))

    tail: List[str] = []
    for name in ("退款率", "在途订单量"):
        metric = by_name.get(name)
        if metric:
            # 这里是句子不是表格，数值与单位之间留个空格读起来才不别扭
            tail.append(f"{name} {metric.current:g} {metric.unit}")
    if tail:
        parts.append("，".join(tail))

    summary = "；".join(parts) + "。" if parts else ""

    highlights = [a.detail for a in report.anomalies if a.detail][:4]
    return summary, highlights


def _publish(report: WeeklyReport, msg: str, detail: str, degraded: bool = False):
    return {
        F.WEEKLY_REPORT: report,
        F.TASK_RESULT: TaskResult(
            task_result=report.render(),
            task_result_explain=detail or msg,
            # 周报本身永远算「结果有效」：指标是确定性算出来的，
            # 摘要降级不影响报表可用，不该把它标成失败让上层去重试。
            task_result_code=0
        ),
        F.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.NARRATE.value,
            execute_result_code=0,
            execute_result_msg=msg,
            execute_exceptions=[detail] if detail else []
        )]
    }


# 子图内部字段（原始订单行、统计窗口）定义在 `sub_weekly_report_state.py`：
# 它们只在本子图的三个节点之间流转，不写回父图 state，理由见那里的注释。
