"""
售后政策引擎：把「客户说了什么 + 订单是什么情况」映射成「该怎么处理」。

为什么是规则引擎而不是让模型判
------------------------------
售后处理会产生**真金白银的退款**。这类决策有三条硬要求，恰好都是大模型不擅长的：

  1. **可复现**：同一个订单、同一个原因，今天和明天的结论必须一致。
     模型有温度、有版本漂移，做不到。
  2. **可解释**：客户问「凭什么不给我退」，要能说出是**哪一条**规则、
     因为**哪个**条件不满足。所以每条命中的条件都写进 `reason_trace`。
  3. **可审计**：出了纠纷要能倒查规则版本和当时的输入。规则是数据（JSON），
     能进版本控制、能 diff、能被人复核；提示词做不到这一点。

所以这里的模型只负责**把结论转述得像人话**，判定完全由规则完成。这也让
「模型只负责转述」这条约束在售后链路上有了第二处落点（第一处是商品问答的
数字校验）——两处合起来才构成一个完整的口径。

规则语义（与 `data/seed/policies.json` 的 `_comment` 一致）
----------------------------------------------------------
  * 同一规则内所有 conditions 之间是 **AND**，全部满足才命中；
  * 规则之间按 `priority` 从大到小，取**第一个**命中的；
  * 条件为空 `{}` 表示无条件命中（兜底规则）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.agent_logger import get_logger

logger = get_logger("policy")

# 词法 parents，不写 .resolve()：后者在 Windows 上会调 os.getcwd()，
# 而 policy 是在请求路径里被 import 的（见 utils/env_loader.py）。
_POLICY_FILE = Path(__file__).parents[2] / "data" / "seed" / "policies.json"
_PRODUCT_FILE = Path(__file__).parents[2] / "data" / "seed" / "products.json"

_TZ_CHINA = timezone(timedelta(hours=8))


# ==================== 输入 ====================

@dataclass
class PolicyFacts:
    """一次售后判定的全部输入。

    刻意做成扁平的事实对象而不是直接传订单 dict：规则引擎的输入越窄，
    规则越好写、越好测，也不会因为上游多返回一个字段而让某条规则意外命中。
    """

    order_id: str = ""
    reason: str = ""                 # 客户的售后原因原文
    amount: float = 0.0              # 订单实付金额（元）
    days_since_signed: int = 0       # 签收后第几天
    status_code: str = ""            # 内部状态码（100~700）
    is_customized: bool = False      # 是否含定制商品
    sku_ids: List[str] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "amount": self.amount,
            "days_since_signed": self.days_since_signed,
            "status_code": self.status_code,
            "is_customized": self.is_customized,
            "reason": self.reason,
        }


# ==================== 输出 ====================

@dataclass
class PolicyMatch:
    """一次判定结果：命中的规则 + 完整的推理轨迹。"""

    policy: Dict[str, Any]
    trace: List[str] = field(default_factory=list)       # 命中规则的条件，逐条
    rejected: List[str] = field(default_factory=list)    # 被跳过的高优先级规则，为什么没命中
    facts: Dict[str, Any] = field(default_factory=dict)

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
        """把模板渲染成给客户看的话。占位符只用事实对象里的值。"""
        template = self.policy.get("reason_template", "") or ""
        values = {
            "amount": f"{self.facts.get('amount', 0):.2f}",
            "days": self.facts.get("days_since_signed", 0),
            "alternative": self.action.get("alternative", ""),
        }
        try:
            return template.format(**values).strip()
        except (KeyError, IndexError, ValueError):
            # 模板里写了没提供的占位符：原样返回，不要让一次文案失误毁掉整条链路
            logger.warning(f"政策 {self.policy_id} 的 reason_template 渲染失败，原样返回")
            return template

    def explain(self) -> str:
        """给坐席/审计看的多行解释。"""
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


def _check_reason_keywords(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    keywords = [str(k) for k in (expected or [])]
    hits = [k for k in keywords if k and k in facts.reason]
    if hits:
        return True, f"售后原因命中关键词：{'、'.join(hits)}"
    sample = "、".join(keywords[:4]) + ("…" if len(keywords) > 4 else "")
    return False, f"售后原因未命中关键词（{sample}）"


def _check_min_amount(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    threshold = float(expected)
    ok = facts.amount >= threshold
    return ok, f"实付金额 {facts.amount:.2f} 元 {_rel(ok, '≥', '<')} {threshold:.2f} 元"


def _check_max_amount(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    threshold = float(expected)
    ok = facts.amount <= threshold
    return ok, f"实付金额 {facts.amount:.2f} 元 {_rel(ok, '≤', '>')} {threshold:.2f} 元"


def _check_within_days(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    limit = int(expected)
    ok = facts.days_since_signed <= limit
    return ok, f"签收后第 {facts.days_since_signed} 天 {_rel(ok, '≤', '>')} {limit} 天"


def _check_min_days(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    limit = int(expected)
    ok = facts.days_since_signed >= limit
    return ok, f"签收后第 {facts.days_since_signed} 天 {_rel(ok, '≥', '<')} {limit} 天"


def _check_order_status(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    allowed = [str(s) for s in (expected or [])]
    ok = facts.status_code in allowed
    # 文案里带上中文状态，读日志的人不需要再查一遍码表
    from integrations.normalize import STATUS_TEXT

    current = f"{facts.status_code}({STATUS_TEXT.get(facts.status_code, '?')})" if facts.status_code else "未知"
    return ok, f"订单状态 {current} {_rel(ok, '属于', '不属于')} {allowed}"


def _check_is_customized(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    want = bool(expected)
    ok = facts.is_customized is want
    return ok, (
        f"订单{'含' if facts.is_customized else '不含'}定制商品，"
        f"{_rel(ok, '符合', '不符合')}「{'定制' if want else '非定制'}」要求"
    )


def _check_sku_not_in(expected: Any, facts: PolicyFacts) -> Tuple[bool, str]:
    excluded = {str(s) for s in (expected or [])}
    overlap = [s for s in facts.sku_ids if s in excluded]
    ok = not overlap
    return ok, f"订单商品 {facts.sku_ids} {_rel(ok, '未命中', '命中')}排除清单 {sorted(excluded)}"


#: 条件名 -> 判定函数。新增一种条件只需要在这里加一行 + 在 policies.json 里用起来。
CONDITION_CHECKS: Dict[str, Callable[[Any, PolicyFacts], Tuple[bool, str]]] = {
    "reason_keywords": _check_reason_keywords,
    "min_amount": _check_min_amount,
    "max_amount": _check_max_amount,
    "within_days": _check_within_days,
    "min_days": _check_min_days,
    "order_status": _check_order_status,
    "is_customized": _check_is_customized,
    "sku_not_in": _check_sku_not_in,
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
            logger.warning(f"未实现的政策条件类型：{name}")
            continue

        ok, note = check(expected, facts)
        (passed if ok else failed).append(note)

    return (not failed), passed, failed


# ==================== 加载与匹配 ====================

@lru_cache(maxsize=1)
def load_policies() -> Tuple[Dict[str, Any], ...]:
    """加载规则库，按优先级降序排好。"""
    if not _POLICY_FILE.exists():
        logger.error(f"政策规则库不存在：{_POLICY_FILE}")
        return ()

    try:
        with _POLICY_FILE.open("r", encoding="utf-8") as f:
            policies = json.load(f).get("policies", [])
    except Exception as e:  # pragma: no cover
        logger.error(f"读取政策规则库失败：{e}")
        return ()

    policies.sort(key=lambda p: int(p.get("priority", 0) or 0), reverse=True)
    return tuple(policies)


def match(facts: PolicyFacts) -> Optional[PolicyMatch]:
    """按优先级从高到低找第一条命中的规则。

    被跳过的规则也会留下痕迹（`rejected`）——「为什么不是破损包赔」这类问题，
    答案就藏在这里，而不是从结果反推。
    """
    policies = load_policies()
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
                facts=facts.snapshot(),
            )

        label = f"{policy.get('policy_id')} {policy.get('title')}"
        reason = "；".join(failed[:2]) if failed else "条件未满足"
        rejected.append(f"{label}：{reason}")

    logger.warning(f"规则库中没有可命中的规则（连兜底规则都没有）：{facts.snapshot()}")
    return None


# ==================== 定制商品的判定 ====================

@lru_cache(maxsize=1)
def _customizable_skus() -> frozenset:
    """从商品资料里推断哪些 SKU 是定制款。

    没有新增字段，而是从现有的「是否支持刻字」规格文本里判 —— 商品页的这份
    说明本来就要维护，再单独加一个布尔字段等于同一个事实有两处来源，迟早对不上。

    判据是**首字**：`products.json` 里这个字段的写法统一为「支持…定制」或
    「不支持…」，所以看它是不是以「支持」开头即可。

    ⚠️ 不能简单地判 `"不支持" not in text`。定制款的原文是
    「支持激光刻字定制，需在下单备注中填写不超过 6 个字符，**定制商品不支持无理由退货**」——
    后半句里也有「不支持」，一刀切会把定制款判成非定制，方向正好判反。
    """
    if not _PRODUCT_FILE.exists():
        return frozenset()

    try:
        with _PRODUCT_FILE.open("r", encoding="utf-8") as f:
            products = json.load(f).get("products", [])
    except Exception as e:  # pragma: no cover
        logger.warning(f"读取商品资料失败，定制判定按否处理：{e}")
        return frozenset()

    out = set()
    for product in products:
        text = str((product.get("specs") or {}).get("是否支持刻字", "")).strip()
        if text.startswith("支持") and "定制" in text:
            out.add(product.get("sku_id", ""))
    return frozenset(out)


def is_customized(sku_ids: List[str]) -> bool:
    """订单里是否含定制商品。"""
    wanted = {s for s in (sku_ids or []) if s}
    return bool(wanted & _customizable_skus())


# ==================== 时间 ====================

def days_since(moment: str, *, now: Optional[datetime] = None) -> int:
    """从某个时刻到今天过了几个**自然日**。

    用自然日而不是「除以 24 小时取整」：政策写的是「签收 7 天内」，客户理解的
    也是日期。昨天下午签收、今天上午来问，按小时算是 0.7 天、按自然日是 1 天，
    后者才对得上「第几天」这个说法。负数（时间在未来）一律归 0。
    """
    text = (moment or "").strip()
    if not text:
        return 0

    reference = now or datetime.now(_TZ_CHINA)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            then = datetime.strptime(text, fmt).replace(tzinfo=_TZ_CHINA)
            break
        except ValueError:
            continue
    else:
        logger.warning(f"无法解析时间：{text!r}，按第 0 天处理")
        return 0

    return max(0, (reference.date() - then.date()).days)


def reset_cache() -> None:
    """清空缓存（测试里改了规则文件后调用）。"""
    load_policies.cache_clear()
    _customizable_skus.cache_clear()
