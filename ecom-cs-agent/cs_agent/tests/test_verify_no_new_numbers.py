"""
守的是「回答里不许出现无依据数字」这条确定性反幻觉闸门
（`src/rag/answer.py` 的 `verify_no_new_numbers`）。

为什么只校验数字
----------------
措辞、语气、是否答非所问没法机械校验，但「凭空多出一个 3 年质保」能被精确
抓住 —— 而这恰恰是客服场景里最会引发纠纷的一类输出。所以模型输出在写进
最终答复之前，每个数字都必须能在**原文照抄的事实单**里找到。

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

QUESTION = "键盘可以同时连几台设备？"

FACTS = (
    "【资料1】规格参数\n"
    "| 连接方式 | 蓝牙 5.1 / 2.4G 接收器 / USB-C 有线，三模 |\n"
    "| 兼容系统 | Windows / macOS / iOS / Android，可记忆 3 台设备 |\n"
    "| 售价 | 459.00 元 |\n"
)


# ==================== 校验本身 ====================

def test_answer_drawing_numbers_from_facts_passes():
    """事实单里的数字随便引用，措辞怎么改都行（比的是集合，不是位置）。"""
    ok, offending = verify_no_new_numbers(
        "蓝牙 5.1 三模，可记忆 3 台设备，售价 459 元。", FACTS
    )
    assert ok is True
    assert offending == []


def test_fabricated_number_is_rejected_and_the_offender_is_named():
    """凭空冒出的数字必须被抓住，而且要能说出是哪一个。

    注意「3 台设备」这个有依据的数字不该被牵连 —— 报出来的应当是
    **没有依据的那一个**，否则排查时无从下手。
    """
    ok, offending = verify_no_new_numbers(
        "可记忆 3 台设备，整机提供 24 个月质保。", FACTS
    )
    assert ok is False
    assert offending == ["24"]


def test_no_numbers_in_answer_is_not_blocked():
    """回答里一个数字都没有：没敢下结论，不拦，交给置信度层处理。"""
    ok, offending = verify_no_new_numbers("具体参数请以商品详情页为准。", FACTS)
    assert ok is True and offending == []


@pytest.mark.parametrize(
    "answer, facts",
    [
        ("实付 1000 元", "实付 1,000 元"),      # 千分位
        ("实付 459 元", "实付 459.00 元"),      # 尾随小数位
        ("编号 048", "编号 48"),                # 前导零
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
    assert "24" not in extract_numbers(facts), "构造用例的前提：24 个月确实没有依据"

    answer = knowledge_base.ask(
        QUESTION, model_text="这款键盘支持记忆 3 台设备，整机提供 24 个月质保。"
    )

    assert answer.degraded is True, "丢弃模型输出必须留下痕迹"
    assert "24" not in extract_numbers(answer.answer)
    assert answer.answer != "这款键盘支持记忆 3 台设备，整机提供 24 个月质保。"
    assert answer.status == "answered"
    # 回退后的答复就是原文照抄的转述
    assert answer.answer.startswith("根据商品资料")


def test_compliant_model_output_is_used_verbatim(knowledge_base):
    """通过校验的模型输出照用 —— 闸门只拦编造，不拦润色。"""
    model_text = "这款键盘是蓝牙 5.1 三模，共可记忆 3 台设备，通过组合键切换。"
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
