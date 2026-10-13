"""
守的是「审批/复核判定走规则引擎」这条线（`src/integrations/policy.py`
+ `data/seed/purchase_policy.json` / `quality_policy.json`）。

为什么规则引擎值得单独立一个文件
--------------------------------
采购下单和收货入库会改变真金白银和库存账，判定必须**可复现、可解释、可审计**。
这三条恰好都是「模型判定」做不到的，所以判定完全由规则完成、模型只负责抽要素。
规则引擎里的每一处边界（≥ 还是 >、缺数据怎么算、未知条件算不算命中）都直接
对应一次「该审的没审 / 不该审的审了」，值得逐条钉死：

  1. **优先级**：按 priority 降序取第一条命中的；被跳过的更高优先级规则也要
     留痕（「为什么这单没用大额条款」的答案在那里）；
  2. **边界值**：`amount_gte` 含等于（正好 50000 要审）、`qty_lte` 含等于
     （正好 5000 免审）、`defect_rate_gt` 不含等于（正好 2% 不复核）——
     差一个等号就是一个漏审口子；
  3. **缺数据的方向**：交期未知、无报价均价都按「不满足」处理 —— 缺数据时
     放行执行等于绕过审批，宁可多审；
  4. **未知条件**：策略 JSON 里写了引擎没实现的条件名，按「不满足」处理并告警 ——
     静默放行会让一条写错字段名的规则变成「无条件命中」，那是最危险的失败模式；
  5. **兜底**：条件为空的规则必须命中；连兜底都没有时 `match` 返回 None，
     调用方必须当失败处理（本引擎不替调用方决定「没规则=放行」）。

用例直接读真实种子（钉住的是「当前种子 + 引擎」的合体行为），不 mock 规则
文件 —— 规则与引擎对不上正是最该被这组用例抓到的东西。
"""

from __future__ import annotations

from integrations import policy as policy_mod
from integrations.policy import PolicyFacts, PolicyMatch, load_policies, match


def _purchase_facts(**over) -> PolicyFacts:
    """一笔常规采购申请的事实（S001 报价 5.80、非新供应商、交期 7 天）。"""
    base = dict(
        material_code="M100023",
        material_name="冷轧钢板 1.2mm",
        qty=3000,
        unit="kg",
        unit_price=5.80,
        avg_price=5.5067,
        amount=17400.0,
        supplier_code="S001",
        supplier_name="供应商S001",
        is_new_supplier=False,
        lead_time_days=7,
    )
    base.update(over)
    return PolicyFacts(**base)


def _receipt_facts(**over) -> PolicyFacts:
    base = dict(
        material_code="M400301",
        material_name="伺服电机 400W",
        qty_received=920,
        qty_defect=30,
        defect_rate=round(30 / 920, 4),
    )
    base.update(over)
    return PolicyFacts(**base)


# ==================== 加载与兜底 ====================

def test_policies_are_loaded_sorted_by_priority_desc():
    policies = load_policies("purchase_policy")
    assert policies, "种子里的采购策略必须加载得到"
    priorities = [int(p["priority"]) for p in policies]
    assert priorities == sorted(priorities, reverse=True)
    assert policies[-1]["conditions"] == {}, "最后一条必须是空条件兜底规则"


def test_unknown_kind_has_no_policies_and_match_returns_none():
    """规则库整个不存在时 match 返回 None（调用方必须当失败处理），不抛异常。"""
    assert load_policies("no_such_policy_kind") == ()
    assert match("no_such_policy_kind", _purchase_facts()) is None


# ==================== 优先级与留痕 ====================

def test_highest_priority_rule_wins_when_several_match():
    """大额与新供应商同时命中：取优先级更高的大额条款（两条都需审，但理由不同）。"""
    pm = match("purchase_policy", _purchase_facts(amount=60000.0, is_new_supplier=True))
    assert pm is not None
    assert pm.policy_id == "PUR-001"
    assert pm.need_approval is True


def test_story_anchor_44800_new_supplier_hits_pur_002_and_traces_pur_001():
    """剧情锚点：8000kg×5.60=44800 走 PUR-002（新供应商）而不是 PUR-001（大额）。

    它是优先级列表里第一条命中的规则，而它前面的大额条款为什么没命中
    （44800 < 50000），要能在 `rejected` 里查到 —— 这是「可解释」的落点。
    """
    pm = match("purchase_policy", _purchase_facts(
        qty=8000, unit_price=5.60, amount=44800.0,
        supplier_code="S012", supplier_name="供应商S012",
        is_new_supplier=True, lead_time_days=10,
    ))
    assert pm is not None
    assert pm.policy_id == "PUR-002"
    assert pm.need_approval is True
    assert any("PUR-001" in line for line in pm.rejected), "被跳过的更高优先级规则要留痕"
    assert "新供应商" in pm.render_reason()


# ==================== 边界值：等号在哪一边 ====================

def test_amount_gte_accepts_exactly_the_threshold():
    """`amount_gte` 含等于：正好 50000 元要审，49999.99 不触发大额。"""
    at = match("purchase_policy", _purchase_facts(qty=10000, amount=50000.0))
    assert at.policy_id == "PUR-001"

    below = match("purchase_policy", _purchase_facts(qty=10000, amount=49999.99))
    assert below.policy_id == "PUR-000", "不到大额线、又超出直采数量 → 兜底常规流程"


def test_qty_lte_accepts_exactly_the_limit_and_gate_above_falls_through():
    """`qty_lte` 含等于：5000 免审直采；5001 落进兜底（=仍需按常规流程走）。"""
    at_limit = match("purchase_policy", _purchase_facts(qty=5000))
    assert at_limit.policy_id == "PUR-005"
    assert at_limit.need_approval is False

    above = match("purchase_policy", _purchase_facts(qty=5001))
    assert above.policy_id == "PUR-000"


def test_defect_rate_gt_does_not_include_the_threshold():
    """`defect_rate_gt` 不含等于：正好 2% 不触发复核，超过 2% 才触发。"""
    exact = match("quality_policy", _receipt_facts(qty_received=1000, qty_defect=20, defect_rate=0.02))
    assert exact.policy_id == "QC-000"
    assert exact.need_approval is False

    over = match("quality_policy", _receipt_facts(qty_received=1000, qty_defect=21, defect_rate=0.021))
    assert over.policy_id == "QC-001"
    assert over.need_approval is True


def test_price_over_avg_pct_boundary_is_exclusive():
    """`price_over_avg_pct` 是「高于」：正好高 15% 不算触发，明显更高才触发。"""
    at, note = policy_mod._check_price_over_avg_pct(
        15, PolicyFacts(unit_price=115.0, avg_price=100.0)
    )
    assert at is False, "正好 15% 不是「高于 15%」"
    assert "未高于" in note

    over, _ = policy_mod._check_price_over_avg_pct(
        15, PolicyFacts(unit_price=116.0, avg_price=100.0)
    )
    assert over is True

    # 规则层面同样验证：高于均价 15% 的单子真的会命中 PUR-004
    hit = match("purchase_policy", _purchase_facts(
        qty=8000, unit_price=5.76, avg_price=5.0, amount=46080.0,
    ))
    assert hit.policy_id == "PUR-004"
    assert "高于" in " ".join(hit.trace)


# ==================== 缺数据的方向：按不满足处理 ====================

def test_unknown_lead_time_is_treated_as_not_satisfied():
    """交期未知（0）≠ 交期短：按不满足处理，不让数据缺失变成「交期过短」的误判。"""
    ok, note = policy_mod._check_lead_time_lt(3, PolicyFacts(lead_time_days=0))
    assert ok is False
    assert "未知" in note

    # 规则层面：交期未知的申请不会命中「交期过短」，且该条会进 rejected 留痕
    pm = match("purchase_policy", _purchase_facts(lead_time_days=0))
    assert pm.policy_id == "PUR-005"
    assert any("PUR-003" in line for line in pm.rejected)


def test_missing_avg_price_is_treated_as_not_satisfied():
    """没有报价均价就不做「高于均价」判定（按不满足），也不按 0 均价算成无穷高。"""
    ok, note = policy_mod._check_price_over_avg_pct(15, PolicyFacts(unit_price=5.8, avg_price=0.0))
    assert ok is False
    assert "均价" in note


# ==================== 未知条件 ====================

def test_unknown_condition_name_is_not_satisfied_and_logged():
    """策略文件里写了引擎没实现的条件名：按**不满足**处理并告警。

    静默放行（按满足）会让一条写错字段名的规则变成「无条件命中」——
    它排在哪就绕过它后面的所有限制，是最危险的失败模式。
    """
    satisfied, passed, failed = policy_mod._eval_conditions(
        {"no_such_condition": 1}, _purchase_facts()
    )
    assert satisfied is False
    assert passed == []
    assert any("未实现" in line for line in failed)


def test_empty_conditions_always_match_as_fallback():
    """空条件规则无条件命中，理由里明说它是兜底 —— 而不是留一段空白解释。"""
    pm = match("purchase_policy", _purchase_facts(
        qty=6000, unit_price=5.5, avg_price=5.0, amount=33000.0, lead_time_days=0,
    ))
    assert pm.policy_id == "PUR-000"
    assert any("兜底" in line for line in pm.trace)


# ==================== 模板渲染 ====================

def test_render_values_formats_numbers_once_and_only_once():
    """格式化口径只有 `render_values` 一处：金额两位小数、不良率百分比。"""
    facts = _receipt_facts()
    values = facts.render_values()
    assert values["defect_rate_pct"] == "3.26%"
    assert values["qty_received"] == "920"
    assert values["qty_defect"] == "30"

    purchase = _purchase_facts(amount=44800.0, avg_price=5.5067)
    pv = purchase.render_values()
    assert pv["amount"] == "44800.00"
    assert pv["avg_price"] == "5.51"
    assert pv["qty"] == "3000"


def test_reason_template_renders_with_real_numbers():
    """渲染出来的理由里带真实数字（审批人看的话术不是空模板）。"""
    pm = match("purchase_policy", _purchase_facts(
        qty=8000, unit_price=5.60, amount=44800.0,
        supplier_code="S012", supplier_name="供应商S012",
        is_new_supplier=True, lead_time_days=10,
    ))
    reason = pm.render_reason()
    assert "供应商S012" in reason
    assert "{" not in reason and "}" not in reason, "占位符必须全部替换掉"


def test_broken_template_falls_back_to_raw_text_instead_of_raising():
    """模板写了没有的占位符：整体原样返回并告警 —— 不半渲染（半渲染会让人
    误以为渲染成功），也不让一次文案失误毁掉整条链路。

    直接构造 PolicyMatch 而不是改 `match()` 返回的对象：后者拿着的是
    `load_policies` 缓存里的 dict，改它会污染后面的用例。
    """
    pm = PolicyMatch(
        policy={"reason_template": "金额 {amount} 元，缺少 {no_such_placeholder}"},
        facts=_purchase_facts().render_values(),
    )
    reason = pm.render_reason()
    assert "{no_such_placeholder}" in reason, "渲染不了的模板原样保留"
    assert reason.startswith("金额"), "至少不抛异常、返回可读文本"


def test_snapshot_only_carries_fields_that_matter():
    """进日志的事实摘要只带判定相关字段（日志里不落整份业务数据）。"""
    snap = _purchase_facts().snapshot()
    assert set(snap) == {
        "material_code", "qty", "unit_price", "amount", "supplier_code",
        "is_new_supplier", "lead_time_days", "defect_rate",
    }
