"""
守的是「阈值以下必须转人工」这条线：`sub_product_qa` 子图在整图里的行为。

三件业务事实：
  * 置信度不足时链路产出的是**转人工工单话术**，不是一条硬编的答案 ——
    而且它算「正常业务结果」（task_result_code = 0），不是失败。判失败会让
    上层把一次正确的兜底当成 bug；
  * 转人工时不给依据（citations 为空），也不许出现任何承诺性数字；
  * 坐席点「商品咨询」按钮时，`task_input` 传的是意图枚举名 `product_qa`，
    真正的问题必须取自**客户最后一条消息** —— 按钮只表达「我要这个能力」，
    UI 传动作、数据从状态里取，这个分工不能被简化成「把 task_input 当问题」。

整图跑在进程内（InMemorySaver + 种子 store），不联网、不启服务。
"""

from __future__ import annotations

from rag import confidence as confidence_mod
from rag.answer import extract_numbers

# 语料里没有「无线充电」：商品名能撞上商品页，诉求完全没有着落 → 必须转人工
UNANSWERABLE_Q = "这款保温杯支持无线充电吗？"

# 坐席工作台的按钮入口：传的是意图枚举名，问题在会话记录里
BUTTON_INPUT = "product_qa"


def test_below_confidence_question_becomes_handoff_instead_of_a_hard_answer(runner):
    """知识库未覆盖的问题：转人工 + 说明原因，而不是硬答一条没依据的内容。"""
    result = runner.start(UNANSWERABLE_Q)

    assert result.error is None
    qa = result.state.get("product_qa")
    assert qa is not None, "商品咨询链路必须把结论回写到主图状态"

    assert qa.status == "handoff"
    assert qa.confidence < confidence_mod.threshold()
    assert qa.citations == [], "转人工时不该再挂一条似是而非的依据"
    assert qa.reason, "要能解释「为什么转人工」，不能只丢一个分数"

    # 转人工是正常业务结果：链路跑完了，结论是「该由人来接」
    assert result.task_result.task_result_code == 0
    assert "人工" in result.task_result.task_result

    # 兜底话术不许出现任何承诺性数字（"48 小时""1-3 个工作日"这类）
    assert extract_numbers(qa.answer) == []


def test_handoff_is_visible_on_the_progress_timeline(runner):
    """时间线上要能看到「转人工」这一步及其原因 —— 否则前端只能靠猜。"""
    result = runner.start(UNANSWERABLE_Q)

    stages = [ev.get("stage") for ev in result.events]
    assert "handoff" in stages

    handoff_ev = next(ev for ev in result.events if ev.get("stage") == "handoff")
    assert handoff_ev.get("detail"), "转人工事件必须带上原因"


def test_button_input_is_an_action_not_a_question(runner):
    """传意图枚举名时，问题取自客户最后一条会话消息，而不是照抄枚举名。"""
    result = runner.start(BUTTON_INPUT)

    assert result.error is None
    qa = result.state.get("product_qa")
    assert qa is not None

    assert qa.question != BUTTON_INPUT
    assert qa.question.strip(), "按钮入口同样要能定位到一个真实问题"
    # 种子会话里客户最后一条消息问的是「灯臂磕碰怎么处理、多久答复」
    assert "磕碰" in qa.question

    # 该问题知识库里有依据（磕碰 → 破损包赔同义词扩展）→ 正常作答并给出处
    assert qa.status == "answered"
    assert qa.citations, "有依据的回答必须可追溯"
    assert result.task_result.task_result_code == 0


def test_answered_task_result_carries_the_citation_footer(runner):
    """作答时最终结果里带出处页脚 —— 「有据可查」是这条链路的核心承诺。"""
    result = runner.start(BUTTON_INPUT)

    qa = result.state.get("product_qa")
    assert qa.status == "answered"
    assert qa.confidence >= confidence_mod.threshold()

    message = result.task_result.task_result
    assert "【出处" in message, "回答应当带上出处编号"
    assert qa.citations[0].snippet, "出处要能看到原文片段，而不是只给一个标题"
