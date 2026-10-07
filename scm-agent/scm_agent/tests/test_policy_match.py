"""
守的是「售后政策匹配与优先级」这条线（`src/integrations/policy.py`）。

规则语义（`data/seed/policies.json`）：
  * 同一规则内所有条件之间是 **AND** —— 少满足一条就整条不命中；
  * 规则之间按 `priority` 从大到小取**第一个**命中 —— 金额 599 的破损单
    必须落到「需审批」的高金额规则，而不是那条能自动退款的小额规则；
  * 条件为空 `{}` 表示无条件命中，兜底规则排最后（优先级最低）；
  * 未知条件类型按**不满足**处理（静默放行会让写错字段名的规则变成无条件命中）。

判定结果必须可解释：命中的每条条件、被跳过的高优先级规则及原因都留在轨迹里。
用例用的是 `policies.json` 里真实存在的政策与 `products.json` 里真实的商品。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from integrations.policy import PolicyFacts, days_since, is_customized, match

_TZ_CHINA = timezone(timedelta(hours=8))

# 种子商品：保温杯不支持刻字定制；磨豆机支持刻字定制
SKU_NORMAL = "SKU-BT500"
SKU_CUSTOM = "SKU-LT200"


# ==================== 优先级：谁压谁 ====================

def test_high_amount_broken_order_goes_to_the_approval_rule():
    """金额 599 的破损单：命中「破损包赔（高金额，需审批）」，而不是小额自动退款那条。

    两条规则的条件几乎一样，区别只在金额区间。谁赢完全由 priority 决定 ——
    如果哪天把「先匹配到的先用」改成了「按文件顺序」，这条会立刻红。
    """
    result = match(PolicyFacts(
        order_id="SO20260320008", reason="灯臂磕碰了，我要退货退款",
        amount=599.0, days_since_signed=3, status_code="400",
    ))

    assert result is not None
    assert result.policy_id == "POL-REFUND-BROKEN-HIGH"
    assert result.action_type == "auto_refund"
    assert result.need_approval is True, "高金额必须走人工审批"
    assert result.priority > 0

    # 被跳过的更高优先级规则要留下痕迹 —— 「为什么不是别的结论」答案在这里
    rejected = " ".join(result.rejected)
    assert "POL-REJECT-DUPLICATE" in rejected
    assert "POL-HANDOFF-EXPIRED" in rejected


def test_low_amount_broken_order_is_auto_refunded():
    """同样破损、金额 199：命中自动退款规则，无需审批。"""
    result = match(PolicyFacts(
        order_id="SO20260315001", reason="杯盖密封圈破损了",
        amount=199.0, days_since_signed=6, status_code="400",
    ))

    assert result is not None
    assert result.policy_id == "POL-REFUND-BROKEN"
    assert result.action_type == "auto_refund"
    assert result.need_approval is False
    assert result.trace, "命中的每条条件都要写进轨迹"


def test_expired_order_hands_off_even_if_it_is_broken():
    """签收 40 天：超出售后时效优先于破损包赔，转人工评估。"""
    result = match(PolicyFacts(
        order_id="SO20260226007", reason="破损了我要退款",
        amount=599.0, days_since_signed=40, status_code="400",
    ))

    assert result is not None
    assert result.policy_id == "POL-HANDOFF-EXPIRED"
    assert result.action_type == "handoff"
    assert result.priority >= match(PolicyFacts(
        reason="破损", amount=599.0, days_since_signed=3, status_code="400",
    )).priority


def test_already_refunded_order_is_rejected_with_top_priority():
    """订单已退款（700）：重复售后申请，最高优先级，直接不受理。"""
    result = match(PolicyFacts(
        order_id="SO20260310005", reason="破损了我要退款",
        amount=199.0, days_since_signed=3, status_code="700",
    ))

    assert result is not None
    assert result.policy_id == "POL-REJECT-DUPLICATE"
    assert result.action_type == "reject"


# ==================== AND 语义与兜底 ====================

def test_all_conditions_must_hold_or_the_rule_is_skipped():
    """破损 + 高金额 + 7 天内，但订单还没发货（200）：两条破损规则都不命中。

    一个条件不满足就整条不命中（AND），而且要能说出是**哪一条**不满足。
    """
    result = match(PolicyFacts(
        order_id="SO20260319003", reason="破损了我要退款",
        amount=599.0, days_since_signed=1, status_code="200",
    ))

    assert result is not None
    assert result.policy_id == "POL-HANDOFF-FALLBACK", "没有规则能下结论时交给人工"

    rejected = " ".join(result.rejected)
    assert "POL-REFUND-BROKEN-HIGH" in rejected
    assert "订单状态" in rejected, "跳过的原因要指到具体条件上"


def test_fallback_rule_matches_when_nothing_else_does():
    """兜底规则条件为空，一定命中 —— 规则库不允许出现「什么结论都给不出」。"""
    result = match(PolicyFacts(reason="我想问一下别的事情", amount=10.0))

    assert result is not None
    assert result.policy_id == "POL-HANDOFF-FALLBACK"
    assert result.action_type == "handoff"
    assert result.trace, "兜底命中同样要有依据说明"


# ==================== 定制商品：同一诉求、两个结论 ====================

def test_customized_products_are_detected_from_the_spec_text():
    """定制款的原文里**后半句也有「不支持」**，判定不能一刀切。"""
    assert is_customized([SKU_CUSTOM]) is True
    assert is_customized([SKU_NORMAL]) is False
    assert is_customized([SKU_NORMAL, SKU_CUSTOM]) is True
    assert is_customized([]) is False


def test_customized_no_reason_return_is_rejected_while_normal_one_is_allowed():
    """同一句「买错了不想要了」：定制款不受理，普通款走七天无理由。"""
    common = dict(
        reason="买错了，不想要了", amount=349.0, days_since_signed=3, status_code="400",
    )

    customized = match(PolicyFacts(sku_ids=[SKU_CUSTOM], is_customized=True, **common))
    assert customized is not None
    assert customized.policy_id == "POL-REFUND-NO-REASON-CUSTOM"
    assert customized.action_type == "reject"
    # 拒绝也要给出替代方案，不能只丢一个「不受理」
    assert customized.action.get("alternative")

    normal = match(PolicyFacts(sku_ids=[SKU_NORMAL], is_customized=False, **common))
    assert normal is not None
    assert normal.policy_id == "POL-REFUND-NO-REASON"
    assert normal.action_type == "return_refund"


# ==================== 时效的算法语义 ====================

def test_days_since_counts_natural_days_not_24h_windows():
    """「签收 7 天内」按自然日算：昨天下午签收、今天上午来问 = 第 1 天。"""
    now = datetime(2026, 3, 17, 0, 30, tzinfo=_TZ_CHINA)
    assert days_since("2026-03-16 23:50:00", now=now) == 1
    assert days_since("2026-03-10 09:00:00", now=now) == 7
    # 时间在未来（数据异常）归 0，不能算出负数让「within_days」意外命中
    assert days_since("2026-03-18 10:00:00", now=now) == 0


def test_unparsable_time_is_treated_as_day_zero():
    assert days_since("不是一个时间") == 0
    assert days_since("") == 0


# ==================== 链路上的落点 ====================

def test_policy_decision_drives_the_after_sale_plan(runner):
    """整图跑一遍：政策编号、金额、判定依据都进了售后方案，客户侧话术已渲染。"""
    result = runner.start("订单 SO20260315001 的保温杯杯盖密封圈破损了，我要退款")
    assert result.error is None

    plan = result.state.get("after_sale")
    assert plan is not None
    assert plan.policy_id == "POL-REFUND-BROKEN"
    assert plan.action_type == "auto_refund"
    assert plan.amount == pytest.approx(199.0)
    assert plan.reason_trace, "「为什么给出这个方案」必须可追溯"
    assert result.task_result.task_result_code == 0
