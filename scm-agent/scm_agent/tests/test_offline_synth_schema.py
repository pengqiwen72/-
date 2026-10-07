"""
守的是「离线合成器输出的 schema 合法性 + 数据驱动」这条线
（`src/llm/offline/`）。

离线模式不是「随便吐一段占位文本」，而是：解析**真实提示词**里内嵌的 schema 与
业务数据 -> 合成目标模型需要的 JSON -> 强制 `model_validate` -> 返回。
调用方（7 个 `llm_*.py`）走的是和真实模型完全相同的解析路径，一行都不用改。

两条不能破的性质：
  1. **schema 合法**：喂进去真提示词，回来的 JSON 必须能被对应 pydantic 模型
     解析通过 —— 否则调用方 `model_validate_json` 会抛，或静默退化成默认值；
  2. **数据驱动**：结论要能追溯到输入里的原文（memory 约定第 9 条）。
     返回固定常量的话，面试官换一句对话就会当场露馅，所以这里逐个断言
     「证据出现在 reason / 结论随输入变化」。

用例拿的是**种子数据**（store 里真实的标签体系、会话、订单）拼出的真实提示词，
并通过 monkeypatch 只做一件事：在调用点截获提示词原文，再单独喂给 registry。
"""

from __future__ import annotations

import json
import re

import pytest

from llm.offline.registry import build_payload
from models.scm_models import (
    ChatHistory,
    ChatMessage,
    CustomerInfo,
    CustomerProfile,
    ReplySuggestion,
    ScheduleSuggestion,
    TagSuggestion,
)

CUSTOMER_EXTERNAL_ID = "wx_ext_001"
FOLLOW_USER_ID = "CS001"
CUSTOMER_UNION_ID = "union_ext_001"


# ==================== 装置 ====================

@pytest.fixture
def store_objects():
    """灌好种子的 store，并按各链路的取数方式把模型对象取出来。"""
    from store.seed_loader import load_seed_data
    from store.store_provider import get_fallback_store

    load_seed_data(get_fallback_store())

    from tools import store_tool as st

    return {
        "tag_setting": st.get_tag_setting(),
        "customer_tags": st.get_customer_tags(CUSTOMER_EXTERNAL_ID, FOLLOW_USER_ID),
        "customer_info": st.get_customer_info(CUSTOMER_EXTERNAL_ID, FOLLOW_USER_ID),
        "employee_info": st.get_employee_info(FOLLOW_USER_ID),
        "customer_profile": st.get_customer_profile(CUSTOMER_EXTERNAL_ID, FOLLOW_USER_ID),
        "chat_history": st.get_chat_history(CUSTOMER_EXTERNAL_ID, FOLLOW_USER_ID),
        "kf_chat_history": st.get_kf_history(CUSTOMER_EXTERNAL_ID),
        "order_history": st.get_order_history(CUSTOMER_UNION_ID),
    }


def _capture_prompt(monkeypatch, module_name: str, fn, **kwargs):
    """调用一次真实的 llm_* 函数，顺手把它拼好的**提示词原文**截出来。

    只在测试侧替换模块里的 `llm` 引用（源码不动）：调用方怎么拼提示词，
    这里就拿到什么，不做二次拼接 —— 否则测的就不是真实提示词了。
    """
    module = __import__(module_name, fromlist=["llm"])
    real = module.llm
    captured: dict = {}

    class _Spy:
        def invoke(self, prompt, *args, **kws):
            captured["prompt"] = prompt
            return real.invoke(prompt, *args, **kws)

    monkeypatch.setattr(module, "llm", _Spy())
    result = fn(**kwargs)
    return captured["prompt"], result


def _assert_schema_valid(prompt: str, model_cls):
    """真提示词 -> registry -> 调用方同一套解析路径。"""
    payload = build_payload(prompt)

    # "{}" 是「认不出目标模型」的降级返回，说明合成器注册表与该提示词脱节了
    assert payload and payload != "{}", "离线合成器没有识别出这个提示词的目标结构"

    parsed = model_cls.model_validate_json(payload)
    return parsed, payload


# ==================== 标签建议 ====================

def test_tag_suggestion_is_schema_valid_and_ids_come_from_the_tag_setting(
    monkeypatch, store_objects
):
    """标签建议必须只使用标签体系里定义过的 ID，且每个标签都给出原因。"""
    from llm.llm_suggest_tag import llm_tag_suggest

    prompt, _ = _capture_prompt(
        monkeypatch, "llm.llm_suggest_tag", llm_tag_suggest,
        tag_setting=store_objects["tag_setting"],
        customer_tags=store_objects["customer_tags"],
        chat_history=store_objects["chat_history"],
        kf_chat_history=store_objects["kf_chat_history"],
        order_history=store_objects["order_history"],
    )
    parsed, _ = _assert_schema_valid(prompt, TagSuggestion)

    known_ids = {t.tag_id for t in store_objects["tag_setting"].tag_setting}
    assert known_ids

    suggestions = list(parsed.tag_ids_add) + list(parsed.tag_ids_remove)
    assert suggestions, "种子会话里既有刻字诉求也有时效诉求，应当给出建议"
    for item in suggestions:
        assert item.tag_id in known_ids, f"建议了未定义的标签 {item.tag_id}"
        assert item.tag_id and item.tag_name and item.tag_reason.strip()


def test_tag_suggestion_reason_quotes_the_conversation_evidence(
    monkeypatch, store_objects
):
    """结论要能被核对：建议的 reason 里出现的是会话原文里的词，不是通用套话。"""
    from llm.llm_suggest_tag import llm_tag_suggest

    prompt, _ = _capture_prompt(
        monkeypatch, "llm.llm_suggest_tag", llm_tag_suggest,
        tag_setting=store_objects["tag_setting"],
        customer_tags=store_objects["customer_tags"],
        chat_history=store_objects["chat_history"],
        kf_chat_history=store_objects["kf_chat_history"],
        order_history=store_objects["order_history"],
    )
    parsed, _ = _assert_schema_valid(prompt, TagSuggestion)

    custom = [i for i in parsed.tag_ids_add if i.tag_id == "TAG-BEHA-CUSTOM"]
    assert custom, "会话里客户明确问了「可以刻字吗」，应命中偏好定制款"
    assert "刻字" in custom[0].tag_reason, "原因里要引用客户原话里的证据"


# ==================== 客户画像 ====================

def test_profile_suggestion_is_schema_valid_and_merges_new_evidence(
    monkeypatch, store_objects
):
    """画像建议的结构合法：旧画像原样保留 + 从会话/订单新抽出的证据。

    「旧画像必须原样保留」这半条专门守 `PromptView.find_last`：提示词里
    `{example_json}` 排在真实 `{customer_profile}` 之前，两者顶层键都是
    `profile_items`。用 `find`（取第一块）会把示例实例当成旧画像 —— 存量里的
    「潜在需求」「关联关系」被顶掉、示例独有的「累计消费金额」「促销参与度」
    混进结果。断言取的是**只有其中一边有**的项，撞名回归会立刻现形。
    """
    from llm.llm_suggest_profile import llm_profile_suggest

    prompt, _ = _capture_prompt(
        monkeypatch, "llm.llm_suggest_profile", llm_profile_suggest,
        chat_history=store_objects["chat_history"],
        kf_chat_history=store_objects["kf_chat_history"],
        order_history=store_objects["order_history"],
        customer_tags=store_objects["customer_tags"],
        customer_profile=store_objects["customer_profile"],
    )
    parsed, _ = _assert_schema_valid(prompt, CustomerProfile)

    assert parsed.profile_items
    for item in parsed.profile_items:
        assert item.item_name.strip() and item.item_value.strip()

    # 新证据来自会话（从对话里抽出的金额）与订单（近期下单情况）
    produced_names = {i.item_name for i in parsed.profile_items}
    assert any("会话" in name for name in produced_names)
    assert any("订单" in i.item_value for i in parsed.profile_items)
    assert parsed.model_dump() != CustomerProfile.get_example_instance().model_dump()

    result_map = {i.item_name: i.item_value for i in parsed.profile_items}
    seed_map = {i.item_name: i.item_value for i in store_objects["customer_profile"].profile_items}

    # ① 只在旧画像里有的项（示例实例里没有）必须原样保留
    seed_only = {n: v for n, v in seed_map.items() if n not in {
        i.item_name for i in CustomerProfile.get_example_instance().profile_items
    }}
    assert seed_only, "前置条件：种子画像应有示例实例之外的项"
    for name, value in seed_only.items():
        assert result_map.get(name) == value, f"存量画像项被丢掉或改写：{name}"

    # ② 只在示例实例里有的项（旧画像里没有）不得带着示例原文混进结果
    example_only = {
        i.item_name: i.item_value
        for i in CustomerProfile.get_example_instance().profile_items
        if i.item_name not in seed_map
    }
    assert example_only, "前置条件：示例实例应有旧画像之外的项"
    for name, value in example_only.items():
        assert result_map.get(name) != value, f"结果里混进了示例实例的原文：{name}"


# ==================== 话术建议 ====================

def test_reply_suggestion_is_schema_valid_and_grounded_in_the_knowledge_base(
    monkeypatch, store_objects
):
    """话术建议的回复取自知识库命中条款，reason 里说明出处，坐席可核对。"""
    from llm.llm_suggest_chat import llm_chat_suggest

    prompt, _ = _capture_prompt(
        monkeypatch,
        "llm.llm_suggest_chat",
        llm_chat_suggest,
        customer_info=store_objects["customer_info"],
        employee_info=store_objects["employee_info"],
        chat_history=store_objects["chat_history"],
    )
    parsed, _ = _assert_schema_valid(prompt, ReplySuggestion)

    assert parsed.reply_content.strip()
    assert parsed.reply_reason.strip()
    assert "知识库" in parsed.reply_reason and "出处" in parsed.reply_reason
    # 回复要对着客户真问的那句（会话末尾问的是灯臂磕碰与答复时效）
    assert "磕碰" in parsed.reply_reason or "破损" in parsed.reply_content
    assert parsed.model_dump() != ReplySuggestion.get_example_instance().model_dump()


# ==================== 跟进日程 ====================

def test_schedule_suggestion_is_schema_valid_and_time_comes_from_the_conversation(
    monkeypatch,
):
    """日程的时间来自对话里的时间表达，而不是示例常量。

    合成器只在对话里找得到时间表达时才产出真实日程；找不到时的行为由下一条
    用例（`..._without_time_expression_...`）守。
    """
    from llm.llm_suggest_schedule import llm_schedule_suggest

    history = ChatHistory(chat_msgs=[
        ChatMessage(
            sender="客户", receiver="CS001",
            content="破损照片发你了。另外明天下午三点方便的话给我打个电话回访一下",
            msg_time="2026-10-06 09:58:00",
        ),
        ChatMessage(
            sender="CS001", receiver="客户",
            content="好的，我这边安排。", msg_time="2026-10-06 09:59:00",
        ),
    ])
    customer = CustomerInfo(
        external_id=CUSTOMER_EXTERNAL_ID, union_id=CUSTOMER_UNION_ID,
        follow_user_id=FOLLOW_USER_ID, nick_name="李女士",
    )

    prompt, _ = _capture_prompt(
        monkeypatch, "llm.llm_suggest_schedule", llm_schedule_suggest,
        customer_info=customer, chat_history=history,
        current_time="2026-10-06 10:00:00",
    )
    parsed, _ = _assert_schema_valid(prompt, ScheduleSuggestion)

    # 时间格式合法（TimeStr 有 pattern 约束，能解析出来本身就说明格式对）
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", parsed.start_time)
    # 「明天」-> 相对当前时间 +1 天，这是从对话里读出来的，不是常量
    assert parsed.start_time.startswith("2026-10-07")
    assert parsed.duration > 0

    example = ScheduleSuggestion.get_example_instance()
    assert parsed.model_dump() != example.model_dump(), "不许返回示例实例充数"
    assert "回访" in parsed.schedule_reason, "原因里要引用对话里的诉求原文"


def test_schedule_without_time_expression_yields_empty_suggestion_not_the_example(
    monkeypatch,
):
    """对话里没有任何时间依据时：产出**空**建议，而不是退化成示例实例。

    `_empty_schedule` 用「title / start_time 同时为空」表达「没有建议」。若
    `ScheduleSuggestion.start_time` 的 pattern 不接受空串，合成结果会在 registry
    的 `model_validate` 处失败、**静默回退成示例实例** —— 一条「日程标题，包含
    具体事项说明 / 2025-08-01 10:00:00」的假日程，还能通过下游
    `create_schedule_node` 的非空校验被真的建出来。这里盯住「不许回退成示例实例」。
    """
    from llm.llm_suggest_schedule import llm_schedule_suggest

    history = ChatHistory(chat_msgs=[
        ChatMessage(
            sender="客户", receiver="CS001",
            content="灯臂磕碰了，怎么处理？", msg_time="2026-10-06 09:58:00",
        ),
        ChatMessage(
            sender="CS001", receiver="客户",
            content="我这边给您登记售后。", msg_time="2026-10-06 09:59:00",
        ),
    ])
    customer = CustomerInfo(
        external_id=CUSTOMER_EXTERNAL_ID, union_id=CUSTOMER_UNION_ID,
        follow_user_id=FOLLOW_USER_ID, nick_name="李女士",
    )

    prompt, _ = _capture_prompt(
        monkeypatch, "llm.llm_suggest_schedule", llm_schedule_suggest,
        customer_info=customer, chat_history=history,
        current_time="2026-10-06 10:00:00",
    )
    parsed, _ = _assert_schema_valid(prompt, ScheduleSuggestion)

    assert parsed.title == "" and parsed.start_time == "", \
        "没有时间依据就不该生成日程（下游以此判空跳过）"
    assert parsed.model_dump() != ScheduleSuggestion.get_example_instance().model_dump(), \
        "不许回退成示例实例充数"
    assert parsed.schedule_reason.strip(), "要说清楚为什么没生成"

    # 放宽的只是「允许空串」，不是放开格式校验：坏格式仍要被拦下
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScheduleSuggestion.model_validate({"start_time": "2026/10/07 15:00"})


# ==================== 降级契约 ====================

def test_unrecognized_prompt_degrades_to_empty_object_instead_of_crashing():
    """认不出目标结构的提示词：返回空对象，而不是抛异常把图打崩。

    `{}` 对调用方是「没合成出来」，随后由各自的默认值兜住；抛异常则会中断
    整条链路 —— 降级顺序是：合成结果 -> 示例实例 -> 空对象。
    """
    payload = build_payload("这是一段没有任何 schema 定义的普通文本。")
    assert json.loads(payload) == {}
