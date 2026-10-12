"""
守的是「回答里不许出现无依据数字」这条确定性反幻觉闸门
（`src/rag/answer.py` 的 `verify_no_new_numbers`）。

为什么只校验数字
----------------
措辞、语气、是否答非所问没法机械校验，但「凭空多出一句『复核 48 小时内
完成』」能被精确抓住 —— 而这恰恰是采购场景里最会引发事故的一类输出
（照它去排产就会缺料）。所以模型输出在写进最终答复之前，每个数字都必须
能在**原文照抄的事实单**里找到。

三条语义边界：
  * 比的是数字**集合的包含关系**，不是逐位置比对（改写措辞是允许的）；
  * 数字先归一化（千分位、尾随 .0、前导零），写法不同不代表新数字；
  * 校验不过就整体丢弃模型输出、回退确定性转述，并记 degraded。
"""

from __future__ import annotations

import pytest

from rag.answer import (
    build_facts_block,
    extract_numbers,
    restate_from_facts,
    verify_no_new_numbers,
)

QUESTION = "采购申请超过多少金额需要主管审批？"

FACTS = (
    "【资料1】审批规则\n"
    "金额达到 50000 元及以上需要主管审批；报价高于近期均价超过 15% 的，"
    "同样进入审批。\n"
    "【资料2】收货与验收\n"
    "不良率超过 2% 时进入人工复核，复核结论写进收货单，事后不可修改。\n"
)


# ==================== 校验本身 ====================

def test_answer_drawing_numbers_from_facts_passes():
    """事实单里的数字随便引用，措辞怎么改都行（比的是集合，不是位置）。"""
    ok, offending = verify_no_new_numbers(
        "审批线是 50000 元，报价高出均价 15% 的同样要审批；"
        "不良率 2% 是复核线。",
        FACTS,
    )
    assert ok is True
    assert offending == []


def test_fabricated_number_is_rejected_and_the_offender_is_named():
    """凭空冒出的数字必须被抓住，而且要能说出是哪一个。

    注意「50000 元」「15%」这些有依据的数字不该被牵连 —— 报出来的应当是
    **没有依据的那一个**，否则排查时无从下手。
    """
    ok, offending = verify_no_new_numbers(
        "金额达到 50000 元需要审批，复核结论 48 小时内给出。", FACTS
    )
    assert ok is False
    assert offending == ["48"]


def test_no_numbers_in_answer_is_not_blocked():
    """回答里一个数字都没有：没敢下结论，不拦，交给置信度层处理。"""
    ok, offending = verify_no_new_numbers("具体口径请以制度原文为准。", FACTS)
    assert ok is True and offending == []


@pytest.mark.parametrize(
    "answer, facts",
    [
        ("审批线 50000 元", "审批线 50,000 元"),   # 千分位
        ("报价 5 元", "报价 5.00 元"),              # 尾随小数位
        ("单号 048", "单号 48"),                    # 前导零
    ],
)
def test_number_writings_are_normalized_before_comparison(answer, facts):
    """同一个数字的不同写法是同一个数字 —— 归一化避免「标点差异」式的误报。"""
    ok, offending = verify_no_new_numbers(answer, facts)
    assert ok is True, offending


def test_extract_numbers_normalizes_forms():
    """抽取即归一：去千分位、去尾随 .0、去前导零。"""
    assert extract_numbers("1,234.50 元") == ["1234.5"]
    assert extract_numbers("共 300 元整") == ["300"]


# ==================== 与作答路径的联动 ====================

def test_noncompliant_model_output_is_discarded_in_favor_of_restatement(knowledge_base):
    """模型编造数字 -> 整段丢弃，回退为事实单的确定性转述，并记 degraded。

    这是这条闸门在链路里的真实位置：宁可给一句没有润色过的原句，
    也不给一句读起来很顺但编了参数的答复。
    """
    ranked = knowledge_base.search(QUESTION)
    facts = build_facts_block(ranked)
    fabricated = next(n for n in ("777", "888", "999") if n not in extract_numbers(facts))
    model_text = f"金额达到 {fabricated} 元及以上需要主管审批。"

    answer = knowledge_base.ask(QUESTION, model_text=model_text)

    assert answer.degraded is True, "丢弃模型输出必须留下痕迹"
    assert fabricated not in extract_numbers(answer.answer)
    assert answer.answer != model_text
    assert answer.status == "answered"
    # 回退后的答复就是原文照抄的转述
    assert answer.answer.startswith("根据制度与资料")


def test_compliant_model_output_is_used_verbatim(knowledge_base):
    """通过校验的模型输出照用 —— 闸门只拦编造，不拦润色。

    模型文本从**本次检索到的事实**里拼出来（措辞自由组织，数字全部有依据），
    这样换语料后依然成立：硬编一句「不良率 5%」在语料改版的那天就会静默失效 ——
    被拦的是它引用的数字，而测试还以为拦的是别的东西。
    """
    ranked = knowledge_base.search(QUESTION)
    model_text = "据资料，" + restate_from_facts(QUESTION, ranked)
    answer = knowledge_base.ask(QUESTION, model_text=model_text)

    assert answer.answer == model_text
    assert answer.degraded is False
    assert answer.status == "answered"


def test_offline_restatement_never_introduces_numbers(knowledge_base):
    """确定性转述天然满足数字校验 —— 它只用原文句子，一个字都不改写。"""
    ranked = knowledge_base.search(QUESTION)
    facts = build_facts_block(ranked)

    restated = restate_from_facts(QUESTION, ranked)
    assert restated
    ok, offending = verify_no_new_numbers(restated, facts)
    assert ok is True, offending

    # 离线整链路的回答同样要过这一关
    answer = knowledge_base.ask(QUESTION)
    assert verify_no_new_numbers(answer.answer, facts)[0] is True
