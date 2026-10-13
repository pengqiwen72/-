"""
采购与质检的规则引擎：把「这笔申请 / 这批到货是什么情况」映射成「要不要人工介入」。

为什么是规则引擎而不是让模型判
------------------------------
采购下单和收货入库都会**改变真金白银和库存账**。这类判定有三条硬要求，
恰好都是大模型不擅长的：

  1. **可复现**：同样的申请要素、同样的到货数字，今天和明天的结论必须一致。
     模型有温度、有版本漂移，做不到。
  2. **可解释**：审批人问「凭什么这单要审、那单不用」，要能说出是**哪一条**
     规则、因为**哪个**条件成立或不成立。所以命中的条件写进 `trace`，
     被跳过的高优先级规则也逐条留痕（`rejected`）。
  3. **可审计**：规则是数据（`data/seed/purchase_policy.json` /
     `quality_policy.json`），能进版本控制、能 diff、能被人复核；
     提示词做不到这一点。

所以链路上模型只负责**抽取要素**（PurchaseDraft / ReceiptDraft），判定完全
由规则完成 —— 「抽取错一个字段还能澄清，策略判错一次就是真金白银」。

规则语义（与两个策略 JSON 的 `_comment` 一致）
--------------------------------------------
  * 同一规则内所有 conditions 之间是 **AND**，全部满足才命中；
  * 规则之间按 `priority` 从大到小，取**第一个**命中的；
  * 条件为空 `{}` 表示无条件命中（兜底规则 —— 每个策略文件都必须有它，
    否则申请可能落进「无规则命中」的缝里，而那条缝的默认行为没人定义过）。

条件名是**数据文件与引擎之间的契约**：策略 JSON 的 `conditions` 里写的就是
`CONDITION_CHECKS` 字典的键。写错键名 = 一律按「不满足」处理并告警 ——
静默放行会让一条写错字段名的规则变成「无条件命中」，那是最危险的失败模式。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.agent_logger import get_logger

logger = get_logger("policy")

# 词法 parents，不写 .resolve()：后者在 Windows 上会调 os.getcwd()，
# 而这个模块是在请求路径里被 import 的（见 utils/env_loader.py）。
_SEED_DIR = Path(__file__).parents[2] / "data" / "seed"


# ==================== 输入 ====================

@dataclass
class PolicyFacts:
    """一次判定的全部输入（采购审批与收货质检共用同一个事实对象）。

    刻意做成扁平的事实对象而不是直接传计划模型：规则引擎的输入越窄，
    规则越好写、越好测，也不会因为上游多带一个字段而让某条规则意外命中。

    两类链路各用其中一部分字段（采购读金额/供应商那一组，质检读不良那一组）；
    共用一个对象是因为模板占位符的取值口径也只能有一处 —— 那就是
    `render_values()`。
    """

    # ---- 采购申请 ----
    material_code: str = ""
    material_name: str = ""
    qty: float = 0.0
    unit: str = ""
    unit_price: float = 0.0            # 本单成交单价（元）
    avg_price: float = 0.0             # 同物料各家报价均价（元）；无报价为 0
    amount: float = 0.0                # 申请金额（元）= qty × unit_price
    supplier_code: str = ""
    supplier_name: str = ""
    is_new_supplier: bool = False
    lead_time_days: int = 0            # 承诺交期（天）；0 表示未知

    # ---- 收货质检 ----
    qty_received: int = 0
    qty_defect: int = 0
    defect_rate: float = 0.0           # 不良率（小数）

    def snapshot(self) -> Dict[str, Any]:
        """进日志用的事实摘要 —— 只带会出现在判定里的字段。"""
        return {
            "material_code": self.material_code,
            "qty": self.qty,
            "unit_price": self.unit_price,
            "amount": self.amount,
            "supplier_code": self.supplier_code,
            "is_new_supplier": self.is_new_supplier,
            "lead_time_days": self.lead_time_days,
            "defect_rate": self.defect_rate,
        }

    def render_values(self) -> Dict[str, str]:
        """`reason_template` 的占位符取值表 —— 唯一渲染口径。

        全部先格式化成字符串：模板里写 `{amount}` 就该得到「44800.00」，
        写 `{defect_rate_pct}` 就该得到「3.26%」—— 把格式化收在这一处，
        模板作者不用关心 Python 的格式说明符，也不会出现两种舍入口径
        （「判定说 3.26%、文案写 3.3%」这种自相矛盾迟早被演示出来）。

        ⚠️ 模板里用了这里没有的占位符，渲染会失败并按原文返回 ——
        两个策略 JSON 的 `_comment` 里写明了「只用事实对象里有的值」，
        新增模板前先对一遍这个字典的键。
        """
        return {
            "material_code": self.material_code,
            "material_name": self.material_name,
            "qty": f"{self.qty:g}",
            "unit": self.unit,
            "unit_price": f"{self.unit_price:.2f}",
            "avg_price": f"{self.avg_price:.2f}",
            "amount": f"{self.amount:.2f}",
            "supplier_code": self.supplier_code,
            "supplier_name": self.supplier_name,
            "lead_time_days": str(self.lead_time_days),
            "qty_received": str(self.qty_received),
            "qty_defect": str(self.qty_defect),
            "defect_rate_pct": f"{self.defect_rate:.2%}",
        }


# ==================== 输出 ====================

@dataclass
class PolicyMatch:
    """一次判定结果：命中的规则 + 完整的推理轨迹。"""

    policy: Dict[str, Any]
    trace: List[str] = field(default_factory=list)       # 命中规则的条件，逐条
    rejected: List[str] = field(default_factory=list)    # 被跳过的高优先级规则，为什么没命中
    facts: Dict[str, str] = field(default_factory=dict)  # render_values() 的格式化快照

    @property
    def policy_id(self) -> str:
        return self.policy.get("policy_id", "")

    @property
    def title(self) -> str:
        return self.policy.get("title", "")

    @property
    def action(self) -> Dict[str, Any]:
        return self.policy.get("action", {}) or {}

    @property
    def priority(self) -> int:
        return int(self.policy.get("priority", 0) or 0)

    @property
    def action_type(self) -> str:
        return self.action.get("type", "handoff")

    @property
    def need_approval(self) -> bool:
        return bool(self.action.get("need_approval", False))

    def render_reason(self) -> str:
        """把模板渲染成给审批人看的话。占位符只用事实对象里的值。"""
        template = self.policy.get("reason_template", "") or ""
        try:
            return template.format(**self.facts).strip()
        except (KeyError, IndexError, ValueError):
            # 模板里写了没提供的占位符：原样返回，不要让一次文案失误毁掉整条链路
            logger.warning(f"策略 {self.policy_id} 的 reason_template 渲染失败，原样返回")
            return template

    def explain(self) -> str:
        """给审批人/审计看的多行解释。"""
        lines = [f"命中规则：{self.policy_id} {self.title}（优先级 {self.priority}）"]
        lines.append("判定依据：")
        lines.extend(f"  ✓ {t}" for t in self.trace)
        if self.rejected:
            lines.append("被跳过的高优先级规则：")
            lines.extend(f"  ✗ {t}" for t in self.rejected)
        return "\n".join(lines)


# ==================== 条件判定 ====================

def _rel(ok: bool, yes: str, no: str) -> str:
    return yes if ok else no


def _check_amount_gte(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    threshold = float(expected)
    ok = facts.amount >= threshold
    return ok, f"申请金额 {facts.amount:.2f} 元 {_rel(ok, '≥', '<')} {threshold:.2f} 元"


def _check_is_new_supplier(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    want = bool(expected)
    who = facts.supplier_name or facts.supplier_code or "（未指定供应商）"
    ok = facts.is_new_supplier is want
    return ok, (
        f"供应商 {who} {'是' if facts.is_new_supplier else '不是'}新供应商，"
        f"{_rel(ok, '符合', '不符合')}「{'新供应商' if want else '非新供应商'}」要求"
    )


def _check_lead_time_lt(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    limit = float(expected)
    if facts.lead_time_days <= 0:
        # 交期未知 ≠ 交期短。缺数据时按「不满足」处理而不是按 0 天判 ——
        # 后者会把一次数据缺失静默变成一条审批要求（或反过来漏审）。
        return False, f"供应商交期未知，无法判定「短于 {limit:g} 天」，按不满足处理"
    ok = facts.lead_time_days < limit
    return ok, f"承诺交期 {facts.lead_time_days} 天 {_rel(ok, '<', '≥')} {limit:g} 天"


def _check_price_over_avg_pct(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    pct = float(expected)
    if facts.avg_price <= 0:
        return False, "没有可比对的报价均价（本物料无报价数据），按不满足处理"
    over = (facts.unit_price / facts.avg_price - 1) * 100
    ok = over > pct
    return ok, (
        f"报价 {facts.unit_price:.2f} 元较同料均价 {facts.avg_price:.2f} 元"
        f"高 {over:.2f}%，{_rel(ok, '高于', '未高于')} {pct:g}%"
    )


def _check_defect_rate_gt(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    threshold = float(expected)
    ok = facts.defect_rate > threshold
    return ok, f"不良率 {facts.defect_rate:.2%} {_rel(ok, '>', '≤')} {threshold:.2%}"


def _check_qty_lte(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    limit = float(expected)
    ok = facts.qty <= limit
    return ok, f"申请数量 {facts.qty:g}{facts.unit} {_rel(ok, '≤', '>')} {limit:g}"


#: 条件名 -> 判定函数。新增一种条件只需要在这里加一行 + 在策略 JSON 里用起来。
CONDITION_CHECKS: Dict[str, Callable[[Any, PolicyFacts], Tuple[bool, str]]] = {
    "amount_gte": _check_amount_gte,
    "is_new_supplier": _check_is_new_supplier,
    "lead_time_lt": _check_lead_time_lt,
    "price_over_avg_pct": _check_price_over_avg_pct,
    "defect_rate_gt": _check_defect_rate_gt,
    "qty_lte": _check_qty_lte,
}


def _eval_conditions(conditions: Dict[str, Any], facts: PolicyFacts) -> Tuple[bool, List[str], List[str]]:
    """逐条判定，返回 (是否全部满足, 满足的说明, 未满足的说明)。"""
    passed: List[str] = []
    failed: List[str] = []

    for name, expected in (conditions or {}).items():
        check = CONDITION_CHECKS.get(name)
        if check is None:
            # 未知条件一律视为**不满足**：静默放行会让一条写错字段名的规则
            # 变成「无条件命中」，那是最危险的失败模式 —— 它会绕过所有限制。
            failed.append(f"{name}：规则里出现了未实现的条件类型，已按不满足处理")
            logger.warning(f"未实现的策略条件类型：{name}")
            continue

        ok, note = check(expected, facts)
        (passed if ok else failed).append(note)

    return (not failed), passed, failed


# ==================== 加载与匹配 ====================

@lru_cache(maxsize=4)
def load_policies(kind: str) -> Tuple[Dict[str, Any], ...]:
    """加载某类规则库，按优先级降序排好。

    `kind` 就是文件名（purchase_policy / quality_policy）—— 规则文件名即契约，
    新增一类策略不需要改引擎的任何代码。
    """
    path = _SEED_DIR / f"{kind}.json"
    if not path.exists():
        logger.error(f"策略规则库不存在：{path}")
        return ()

    try:
        with path.open("r", encoding="utf-8") as f:
            policies = json.load(f).get("policies", [])
    except Exception as e:  # pragma: no cover
        logger.error(f"读取策略规则库失败（{kind}）：{e}")
        return ()

    policies.sort(key=lambda p: int(p.get("priority", 0) or 0), reverse=True)
    return tuple(policies)


def match(kind: str, facts: PolicyFacts) -> Optional[PolicyMatch]:
    """按优先级从高到低找第一条命中的规则。

    被跳过的规则也会留下痕迹（`rejected`）—— 「为什么这单不用大额审批」
    这类问题，答案就藏在这里，而不是从结果反推。

    返回 None 表示**连兜底规则都没命中**（策略文件缺失或没有空条件规则）。
    调用方必须把它当失败处理：判定不可用时放行执行，等于绕过了所有审批。
    """
    policies = load_policies(kind)
    if not policies:
        return None

    rejected: List[str] = []

    for policy in policies:
        conditions = policy.get("conditions") or {}
        satisfied, passed, failed = _eval_conditions(conditions, facts)

        if satisfied:
            return PolicyMatch(
                policy=policy,
                trace=passed or ["该规则无条件限制，作为兜底命中"],
                rejected=rejected,
                facts=facts.render_values(),
            )

        label = f"{policy.get('policy_id')} {policy.get('title')}"
        reason = "；".join(failed[:2]) if failed else "条件未满足"
        rejected.append(f"{label}：{reason}")

    logger.warning(
        f"「{kind}」规则库中没有可命中的规则（连兜底规则都没有）：{facts.snapshot()}"
    )
    return None


def reset_cache() -> None:
    """清空缓存（测试里改了规则文件后调用）。"""
    load_policies.cache_clear()
