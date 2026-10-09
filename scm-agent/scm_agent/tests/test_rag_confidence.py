"""
守的是「检索置信度与阈值边界」这条线（`src/rag/`）。

三件事不能被改坏，改了任意一件，「检索不到就转人工」的兜底就会失效：
  * 重排分是**绝对分** `x/(x+3)`，不是按 query 做的 min-max 归一化 ——
    归一化会让每次查询的 top1 恒等于 1.0，固定阈值再也拦不住任何东西；
  * 置信度是 top1 / margin / 覆盖率三项加权，量纲必须落在 [0,1] 内；
  * 诉求词覆盖率的判负线只有「等于 0」这一条，且阈值 `RAG_CONFIDENCE_THRESHOLD`
    可调但语义是**绝对阈值**（低于它才转人工，等于不转）。

断言一律断在业务语义上（转人工/作答/是否有依据、跨查询分数可比），
不锁具体数值常量 —— 调语料、调权重都可能让数字变，语义不该变。
"""

from __future__ import annotations

import pytest

from rag import confidence as confidence_mod
from rag.expansion import expand_query
from utils.tokenize import tokenize

# 语料里真实存在、能被完整回答的两个问题（借用了商品名，检索有明确依据）
KEYBOARD_Q = "键盘可以同时连几台设备？"
WOUND_Q = "灯臂磕碰了怎么处理？多久答复？"

#: 诉求词候选：与业务无关的荒诞词组，任何一本业务语料都不该收录。
#: 用例会**逐个校验**它在当前语料里真的不存在（见过一次算我输）。
_ABSENT_TERM_CANDIDATES = ("量子纠缠", "星际跃迁", "时光倒流", "永动机")


# ---------------------------------------------------------------- 重排绝对分

def test_rerank_scores_are_absolute_not_query_normalized(knowledge_base):
    """重排分是绝对分：不同查询的 top1 各不相同，且都进不了 1.0。

    如果哪天有人把它改成「按本次查询的候选集做 min-max 归一化」，
    每个查询的 top1 都会变成 1.0，这个用例会立刻红。
    """
    strong = knowledge_base.search(KEYBOARD_Q)
    moderate = knowledge_base.search(WOUND_Q)
    assert strong and moderate

    strong_top1 = strong[0].rerank
    moderate_top1 = moderate[0].rerank

    for top1 in (strong_top1, moderate_top1):
        assert 0.0 < top1 < 1.0, f"绝对分应当落在 (0,1)，实际 {top1}"

    # 查询之间的分数可比：匹配更强的查询，top1 明显更高。
    # 归一化后这两个值会都等于 1.0，这条断言是它的反面。
    assert strong_top1 > moderate_top1
    assert abs(strong_top1 - moderate_top1) > 0.01


def test_search_results_are_ranked_descending(knowledge_base):
    """检索结果按重排分降序 —— 置信度取的就是 ranked[0]，顺序错了全盘错。"""
    ranked = knowledge_base.search(KEYBOARD_Q)
    scores = [item.rerank for item in ranked]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------- 置信度组成

def test_confidence_composes_three_signals_and_stays_in_unit_interval(knowledge_base):
    """置信度 = top1 / margin / 覆盖率 加权和，量纲落在 (0,1]。"""
    ranked = knowledge_base.search(KEYBOARD_Q)
    conf, detail = confidence_mod.compute(ranked, KEYBOARD_Q)

    assert 0.0 < conf <= 1.0
    for key in ("top1", "top2", "margin", "coverage"):
        assert key in detail, f"可解释性分项 {key} 不能丢"

    # margin 就是第一名与第二名的差距，且不为负
    assert detail["margin"] == pytest.approx(
        max(0.0, detail["top1"] - detail["top2"]), abs=1e-3
    )

    # 三项加权和：用模块自己的权重校验组成关系，而不是照抄某次的数值
    expected = (
        confidence_mod.W_TOP1 * detail["top1"]
        + confidence_mod.W_MARGIN * detail["margin"]
        + confidence_mod.W_COVERAGE * detail["coverage"]
    )
    assert conf == pytest.approx(expected, abs=1e-3)


# ---------------------------------------------------------------- 判负线与阈值

def test_unanswerable_question_is_vetoed_by_ask_coverage_not_by_a_low_score(knowledge_base):
    """「资料里压根没有」的问题：排序分数不低，但诉求词覆盖率为 0 —— 判负转人工。

    这是整条兜底里最容易被误解的一处：拦住它的**不是**置信度阈值那个数字，
    而是「诉求词一个都没着落」这条语义判据。所以这里先断言 top1 高于阈值
    （分数本身够得着），再断言置信度被强制为 0。

    问句与「保温杯支持无线充电吗」同构：真文档名（指代词，决定 top1「像谁」）
    + 一个语料里绝对不存在的诉求词。文档名从语料里现取、诉求词逐个校验
    不在语料里，是为了换语料后这条用例依然成立 —— 硬编的词组会在语料
    更新后静默失效，那时断言红的地方就不是它要守的语义了。
    """
    title = knowledge_base.chunks[0].doc_title

    # 只比双字及以上的 token：覆盖率判据本身就是这么算的（单字噪声不算数）
    def _words(text: str) -> set:
        return {t for t in tokenize(text) if len(t) >= 2}

    corpus_words: set = set()
    for chunk in knowledge_base.chunks:
        corpus_words |= _words(chunk.text)
    absent = next(
        (w for w in _ABSENT_TERM_CANDIDATES if not (_words(w) & corpus_words)),
        None,
    )
    assert absent, f"语料变了：候选诉求词全被收录，请补新的荒诞词组（{_ABSENT_TERM_CANDIDATES}）"

    query = f"{title}{absent}？"
    ranked = knowledge_base.search(query)
    conf, detail = confidence_mod.compute(ranked, query)

    assert detail["top1"] > confidence_mod.threshold(), (
        "该问题的 top1 是靠文档名（指代词）撞上真实文档撑起来的，本来就不低"
    )
    assert detail["ask_coverage"] == 0.0
    assert conf == 0.0
    assert "诉求" in (detail.get("reason") or ""), "判负原因要说清是诉求没着落"

    answer = knowledge_base.ask(query)
    assert answer.status == "handoff"
    assert answer.citations == [], "转人工时不该再给出一条似是而非的依据"
    assert answer.reason


def test_partial_ask_coverage_does_not_trigger_the_veto(knowledge_base):
    """诉求词只要有一点着落就不判负 —— 判负线是「等于 0」而不是某个调出来的值。

    「灯臂磕碰了怎么处理？多久答复？」这种复合长句的 ask_coverage 只有零点几，
    但资料里确实有对应条款，必须正常作答而不是误伤成转人工。
    """
    ranked = knowledge_base.search(WOUND_Q)
    conf, detail = confidence_mod.compute(ranked, WOUND_Q)

    assert 0.0 < detail["ask_coverage"] < 1.0
    assert conf > 0.0

    answer = knowledge_base.ask(WOUND_Q)
    assert answer.status == "answered"
    assert answer.citations


def test_threshold_is_an_absolute_gate_that_env_can_tune(knowledge_base, monkeypatch):
    """阈值语义是绝对的：同一个问题，阈值抬高后立刻转人工。

    能这样「同一个问题换来两种结论」，前提正是重排分不随查询归一化 ——
    若归一化，top1 恒为 1.0，任何阈值都拦不住。
    """
    answered = knowledge_base.ask(KEYBOARD_Q)
    assert answered.status == "answered"
    assert answered.confidence > 0.0

    monkeypatch.setenv("RAG_CONFIDENCE_THRESHOLD", "0.99")
    handoff = knowledge_base.ask(KEYBOARD_Q)

    assert handoff.status == "handoff"
    assert handoff.citations == []
    assert 0.0 < handoff.confidence < 0.99
    assert handoff.reason


def test_should_handoff_boundary_is_strict_less_than():
    """边界语义：严格低于阈值才转人工，恰好等于阈值不转。"""
    limit = confidence_mod.threshold()
    assert 0.0 < limit < 1.0

    assert confidence_mod.should_handoff(limit - 0.01, limit=limit) is True
    assert confidence_mod.should_handoff(limit, limit=limit) is False
    assert confidence_mod.should_handoff(limit + 0.01, limit=limit) is False


# ---------------------------------------------------------------- 同义词安全阀

def test_synonym_expansion_is_the_safety_valve_for_colloquial_wording(knowledge_base):
    """客户说「磕碰」、语料写「破损」：扩展表把两者接上，检索才能命中条款。

    同义词表是判负线的安全阀（memory 约定第 6 条）——把这张表删了或让检索
    与抽句各用各的扩展，这个问题就会从「正常作答」掉成「转人工」。
    """
    expanded, injected = expand_query(WOUND_Q)
    assert "破损" in injected, "口语「磕碰」应当被扩展为语料术语「破损」"
    assert "破损" in expanded

    answer = knowledge_base.ask(WOUND_Q)
    assert answer.status == "answered"
    assert answer.citations, "扩展生效后必须抽得出可引用的原句"
    assert "破损" in answer.citations[0].section, (
        "命中的应该是破损相关的条款，而不是别的售后段落"
    )
