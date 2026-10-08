"""
守的是「阈值以下必须转人工」这条线：`sub_material_qa` 子图在整图里的行为。

三件业务事实：
  * 置信度不足时链路产出的是**转人工话术**，不是一条硬编的答案 ——
    而且它算「正常业务结果」（task_result_code = 0），不是失败。判失败会让
    上层把一次正确的兜底当成 bug；
  * 转人工时不给依据（citations 为空），也不许出现任何承诺性数字；
  * 工作台点「智能问答」按钮时，`task_input` 传的是意图枚举名 `material_qa`
    本身 —— 按钮只表达「我要这个能力」，没有携带问题。正确行为是如实提示
    补充问题（code=1 等补输入），而不是硬编一条示例问题答非所问。

整图跑在进程内（InMemorySaver + 种子 store），不联网、不启服务。
"""

from __future__ import annotations

from rag import confidence as confidence_mod
from rag.answer import extract_numbers

# 按钮入口：传的是意图枚举名本身，没有携带任何问题
BUTTON_INPUT = "material_qa"

# 真实业务问句（语料内本可作答）—— 用来演示「本可作答，但阈值把它拦下了」
REAL_Q = "冷轧钢板的验收标准是什么？"


def _corpus_derived_question(kb) -> str:
    """从当前语料派生一个**必然有依据**的问题。

    硬编具体问题会在换语料时失效（问的那条条款知识库里已经没有了）；改为
    「语料里有什么就问什么」：取第一条正文的开头做问句，换哪本语料都成立。
    首行可能是「**Q：……**」式的问答标题，先剥掉 Markdown 装饰再取前 40 字，
    免得整行太长把查询词权重冲散。
    """
    first_line = kb.chunks[0].body.splitlines()[0].strip().strip("*").strip()
    fragment = first_line.removeprefix("Q：")[:40]
    return f"{fragment}，具体是怎么规定的？"


def test_below_confidence_question_becomes_handoff_instead_of_a_hard_answer(runner, monkeypatch):
    """置信度不足：转人工 + 说明原因，而不是硬答一条没依据的内容。

    这里把阈值顶到 0.99 来制造「置信度不足」（任何真实问题都会落到阈值之下），
    而不是去硬找一个「语料外的问题」—— 换域之后，语料外的问题偶尔仍能蹭到
    高分，「问题造得不够偏」会让这条用例变成假红；阈值才是转人工判定的唯一开关。
    """
    monkeypatch.setenv("RAG_CONFIDENCE_THRESHOLD", "0.99")
    result = runner.start(REAL_Q)

    assert result.error is None
    qa = result.state.get("material_qa")
    assert qa is not None, "问答链路必须把结论回写到主图状态"

    assert qa.status == "handoff"
    assert qa.confidence < confidence_mod.threshold()
    assert qa.citations == [], "转人工时不该再挂一条似是而非的依据"
    assert qa.reason, "要能解释「为什么转人工」，不能只丢一个分数"

    # 转人工是正常业务结果：链路跑完了，结论是「该由人来接」
    assert result.task_result.task_result_code == 0
    assert "人工" in result.task_result.task_result

    # 兜底话术不许出现任何承诺性数字（"几小时答复"这类）
    assert extract_numbers(qa.answer) == []


def test_handoff_is_visible_on_the_progress_timeline(runner, monkeypatch):
    """时间线上要能看到「转人工」这一步及其原因 —— 否则前端只能靠猜。"""
    monkeypatch.setenv("RAG_CONFIDENCE_THRESHOLD", "0.99")
    result = runner.start(REAL_Q)

    stages = [ev.get("stage") for ev in result.events]
    assert "handoff" in stages

    handoff_ev = next(ev for ev in result.events if ev.get("stage") == "handoff")
    assert handoff_ev.get("detail"), "转人工事件必须带上原因"


def test_button_input_is_an_action_not_a_question(runner):
    """传意图枚举名时没有携带问题：如实提示补充，而不是硬编一条答案答非所问。"""
    result = runner.start(BUTTON_INPUT)

    assert result.error is None
    qa = result.state.get("material_qa")
    assert qa is not None

    assert qa.question == "", "按钮入口没有携带问题，不能把枚举名当成问题本身"
    # 没有可回答的内容同样落到 handoff（对下游是同一种结局），但语义是「等补输入」
    assert qa.status == "handoff"
    assert "输入" in qa.answer, "要明确提示用户补一个具体问题"
    assert result.task_result.task_result_code == 1, "缺输入是等补充，不是结果有效"


def test_answerable_question_returns_citations_and_the_source_footer(runner, knowledge_base):
    """有依据的问题：作答 + 出处页脚 —— 「有据可查」是这条链路的核心承诺。"""
    question = _corpus_derived_question(knowledge_base)
    result = runner.start(question)

    assert result.error is None
    qa = result.state.get("material_qa")
    assert qa.status == "answered"
    assert qa.confidence >= confidence_mod.threshold()
    assert qa.citations, "有依据的回答必须可追溯"

    message = result.task_result.task_result
    assert "【出处" in message, "回答应当带上出处编号"
    assert qa.citations[0].snippet, "出处要能看到原文片段，而不是只给一个标题"
