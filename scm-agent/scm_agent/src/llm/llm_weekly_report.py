"""
数据周报的文字部分生成。

**模型在这条链路上只负责说话，不负责算数。**
同环比、退款率、客单价全部由 `sub_weekly_report` 用确定性代码算好，模型拿到的是
一张算完的指标表，产出 `ReportNarrative`（一段摘要 + 几条要点），数值一个都不经它手。

这样切分的理由：算错数属于**确定性错误**，写个断言就能发现；措辞好坏没法自动校验。
把模型的输出面收窄到「不涉及算术的那些字」，它就不可能算错 —— 控制幻觉最有效的
办法不是反复叮嘱它「不要算错」，而是让它压根没有算错的机会。

提示词之外还有第二道闸：调用方会把模型产出的文字喂给
`rag.answer.verify_no_new_numbers` 逐数字比对，凡是没在指标表里出现过的数字，
整段文字直接丢弃、回退成确定性转述。所以这里**允许**模型说错话。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Sequence

from langchain_core.messages import AIMessage

from llm.llm_setting import chat_model as llm
from models.scm_models import ReportAnomaly, ReportMetric, ReportNarrative
from utils.agent_logger import get_logger

logger = get_logger("llm_weekly_report")


def build_report_facts(
    metrics: Sequence[ReportMetric],
    anomalies: Sequence[ReportAnomaly],
    period_start: str,
    period_end: str,
) -> str:
    """把指标打包成**确定性事实单**——同时也是提示词里喂给模型的那份数据。

    同一个字符串有两个用途：① 作为模型的输入；② 作为 `verify_no_new_numbers`
    的比对基准。两处必须是同一份，否则「模型只能引用事实单里的数字」这条约束
    会因为基准不一致而失效（校验用的是 A 版本、模型看的是 B 版本，就会冤枉它）。
    """
    return json.dumps(
        {
            "period_start": period_start,
            "period_end": period_end,
            "metrics": [m.model_dump() for m in metrics],
            "anomalies": [a.model_dump() for a in anomalies],
        },
        ensure_ascii=False,
        indent=2,
    )


def llm_weekly_report(
    metrics: List[ReportMetric],
    anomalies: List[ReportAnomaly],
    period_start: str = "",
    period_end: str = "",
    table: str = "",
    facts: str = "",
    current_time: str = "",
) -> ReportNarrative:
    """生成周报的文字部分（摘要 + 要点）。算数不在这里。"""
    prompt = _weekly_report_instructions.format(
        period_start=period_start,
        period_end=period_end,
        facts_json=facts or build_report_facts(metrics, anomalies, period_start, period_end),
        table=table,
        current_time=current_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        schema_json=ReportNarrative.get_schema_json(),
        example_json=ReportNarrative.get_example_json(),
    )

    logger.debug(f"prompt: {prompt}")

    generated_result: AIMessage = llm.invoke(prompt)

    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        generated_narrative_json = generated_result.content
    else:
        generated_narrative_json = "{}"

    try:
        narrative = ReportNarrative.model_validate_json(generated_narrative_json)
        logger.info(f"generated_narrative: {narrative.model_dump_json()}")
        return narrative
    except Exception as e:
        # 解析失败返回空叙述而不是抛异常：周报的指标与表格是确定性算出来的，
        # 少一段文字不影响坐席使用；让整张周报因为措辞生成失败而拿不到，才是不划算的。
        logger.error(f"生成周报摘要失败: {e}")
        return ReportNarrative()


_weekly_report_instructions = """
你的任务是为一期已经算好的电商运营周报撰写文字部分：ReportNarrative。

这里是 ReportNarrative 的数据结构定义：
-------------------
{schema_json}
-------------------

json对象示例：
-------------------
{example_json}
-------------------

请根据下面的数据，生成 ReportNarrative，具体要求：

1、【重要】您必须回复一个有效JSON对象
2、请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
3、json对象结构必须符合ReportNarrative的定义。请不要在json对象中包含任何未定义的字段。
4、【最重要】所有数字都已经由系统算好了，你**只负责组织语言**，不负责计算：
   - 摘要和要点里出现的每一个数字，都必须能在下面的「指标数据」里原样找到；
   - 禁止自己推算任何新数字，包括自己算同比、自己算占比、自己把两个数相减或相除、自己四舍五入出新的百分比；
   - 需要描述趋势时，直接引用指标里的 delta_pct，不要重新计算它。
5、summary 控制在 2~3 句话：先说成交（订单量与金额），再说退款与在途这类需要关注的情况。
6、highlights 每条一句话，最多 4 条；优先引用 anomalies 中 level 为 warn 的条目。
7、某项指标的 delta_pct 为 null 表示上周没有可比数据，此时**不要**臆测涨跌，也不要拿它跟 0 比较。
8、不要复述整张表格，表格会由系统单独渲染。

本周统计周期：{period_start} ~ {period_end}（当前时间 {current_time}）

这是本周的指标数据，你的文字必须严格基于它：
-------------------
{facts_json}
-------------------

这是系统已经生成的指标表格（仅供你理解口径，不要在回复里重复它）：
-------------------
{table}
-------------------
"""
