"""
幂等保护。

退款是这个系统里唯一**会把钱花出去**的操作，而它的触发路径恰好都很容易重复：

  * 客户连点两下「申请退款」；
  * HITL 的 `interrupt()` 在 resume 时会**从节点第一行重放**，退款执行节点
    如果在中断之前，一次审批就会退两次；
  * 前端 SSE 断线重连，同一个请求又发了一遍。

三重防护，各管一段
------------------
1. **plan_id 确定性生成**（`uuid5(order_id:action_type)`）：同一个「订单 + 动作」
   永远得到同一个 plan_id，不随调用次数变化。它是后面所有记录的锚点，
   也让 recreate 循环收敛到同一单，而不是每轮生成一个新的。
2. **本模块的 guard**：以 plan_id 为键记账。重复请求不再真的调下游，
   直接返回首次的结果并标记 `replayed`。
3. **退款执行放在中断之后的独立节点**：中断前的节点会被重放，有副作用的代码
   绝不能放在那里。这一条是结构上的保证，不依赖 guard 是否正确。

关于「hash 不同就抛冲突」
------------------------
同一个订单 + 同一个动作，但金额或原因变了，这不是重放，是**另一个请求**。
这时静默复用第一次的结果会出错（客户把 199 改成 199.99，我们按 199 退了，
钱少了）；直接执行第二次也可能出错（可能是同一次诉求的重复提交，只是措辞变了）。
两者都赌不起，所以拒绝并转人工 —— 冲突本身就是要人来判的信号。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from langgraph.store.base import BaseStore

from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("idempotency")

IDEMPOTENCY_NAMESPACE = ("idempotency",)
APPROVAL_NAMESPACE = ("approval",)

#: plan_id 的命名空间常量。**不要改**：改了之后所有历史订单的 plan_id 都会变，
#: 存量记录全部对不上，幂等保护等于失效。
_PLAN_NAMESPACE = uuid.UUID("6f1d0f9a-3c4b-4f2e-9a1d-2b7c5e8f0a31")


class IdempotencyConflict(RuntimeError):
    """同一个业务键，但请求内容不同 —— 拒绝执行，转人工。"""

    def __init__(self, key: str, first_hash: str, new_hash: str):
        self.key = key
        self.first_hash = first_hash
        self.new_hash = new_hash
        super().__init__(
            f"业务键 {key} 已存在内容不同的请求（{first_hash[:8]} vs {new_hash[:8]}），"
            f"拒绝执行以免重复退款"
        )


@dataclass
class IdempotencyRecord:
    """一次请求的记账。"""

    key: str
    plan_id: str
    request_hash: str
    status: str = "in_progress"          # in_progress / done / failed
    result: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def replayed(self) -> bool:
        """本次是不是「命中已有记录」而不是首次执行。"""
        return self.status == "done"

    @classmethod
    def from_value(cls, value: Dict[str, Any]) -> "IdempotencyRecord":
        return cls(
            key=value.get("key", ""),
            plan_id=value.get("plan_id", ""),
            request_hash=value.get("request_hash", ""),
            status=value.get("status", "in_progress"),
            result=dict(value.get("result") or {}),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
        )

    def to_value(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "plan_id": self.plan_id,
            "request_hash": self.request_hash,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_key(order_id: str, action_type: str) -> str:
    """业务键：`订单号:动作`。

    刻意不含时间戳与随机数 —— 它要表达的正是「同一个订单的同一个动作」，
    带上时间就不再幂等了。
    """
    return f"{(order_id or '').strip()}:{(action_type or '').strip()}"


def build_plan_id(order_id: str, action_type: str) -> str:
    """确定性方案 ID。

    用 uuid5（基于名字的哈希）而不是 uuid4（随机）：同一个订单 + 动作
    必须算得出同一个 ID，否则「查一下这单之前是不是处理过」就无从查起。
    长度取 16 位十六进制，够用且日志里读得清。
    """
    return uuid.uuid5(_PLAN_NAMESPACE, build_key(order_id, action_type)).hex[:16]


def _short_digest(*parts: Any) -> str:
    """业务要素压成 6 位大写十六进制 —— 单号里的「同一笔」判据就靠它。"""
    joined = ":".join(str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:6].upper()


def build_request_id(
    material_code: str,
    qty: float,
    target_warehouse: str,
    operator_id: str,
    *,
    when: Optional[datetime] = None,
) -> str:
    """确定性采购申请单号：`PR + 日期 + 6 位哈希`。

    为什么单号也要确定性
    --------------------
    单号是采购链路的幂等锚点（plan_id 由它再 uuid5 一层）。如果这里用
    随机数或流水号，同一个申请重发一次就会得到两个单号、两张采购订单 ——
    「重复提交」这个最常见的误操作就没有任何防线了。

    哈希的四个输入就是采购申请的全部业务要素：**物料、数量、到货仓、提报人**。
    改了其中任何一项都是另一笔申请，理应得到新单号；四项都没变就是同一笔，
    落到同一个单号上由 `IdempotencyGuard` 兜住。

    日期进单号是业务惯例（人看单号就知道什么时候提的）。代价是跨天重放
    会得到新单号 —— 这在演示尺度上无所谓，真实系统里同一笔申请也不会
    跨越一天还没处理完。
    """
    digest = _short_digest(material_code, f"{qty:g}", target_warehouse, operator_id)
    day = (when or datetime.now()).strftime("%Y%m%d")
    return f"PR{day}{digest}"


def build_receipt_id(
    po_no: str,
    qty_received: int,
    qty_defect: int,
    *,
    when: Optional[datetime] = None,
) -> str:
    """确定性收货单号：`RC + 日期 + 6 位哈希`。

    与采购申请同一个设计：**单号是幂等锚点**，随机流水号会让同一次到货登记
    重复提交时产生两张收货单，而「多登记一次入库」比「多下一单」更难被发现
    （账实不符要等盘点才暴露）。

    哈希输入取「订单 + 实收 + 不良」三项：同一张采购订单的同一次到货，
    三项都没变就是同一笔；数量改了（比如补录漏记的 20 件）是另一笔登记，
    理应得到新单号，由人工去看两张单是不是同一批货。
    """
    digest = _short_digest(po_no, qty_received, qty_defect)
    day = (when or datetime.now()).strftime("%Y%m%d")
    return f"RC{day}{digest}"


def request_hash(payload: Dict[str, Any]) -> str:
    """把请求内容压成一个稳定的指纹。

    `sort_keys=True` 是必须的：dict 的键序在不同调用路径下可能不同，
    不排序会让「内容完全相同」被判成冲突。
    """
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyGuard:
    """以业务键为锚点的记账本。"""

    def __init__(self, store: Optional[BaseStore] = None):
        self._store = store

    @property
    def store(self) -> BaseStore:
        if self._store is None:
            self._store = get_fallback_store()
        return self._store

    # ---------------------------------------------------------------- 读写

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        if not key:
            return None
        try:
            item = self.store.get(IDEMPOTENCY_NAMESPACE, key)
        except Exception as e:  # pragma: no cover - 记账读不到按「没记录」处理
            logger.warning(f"读取幂等记录失败，按未命中处理：{e}")
            return None
        return IdempotencyRecord.from_value(dict(item.value)) if item else None

    def _put(self, record: IdempotencyRecord) -> None:
        record.updated_at = _now()
        try:
            self.store.put(IDEMPOTENCY_NAMESPACE, record.key, record.to_value())
        except Exception as e:  # pragma: no cover
            # 写不进去是严重问题：这一次的幂等保护会失效。但直接抛异常会让
            # 一次本来能成功的退款失败，所以记错误日志并继续 —— 由告警兜住。
            logger.error(f"幂等记录写入失败，本次请求的幂等保护不生效：{e}")

    # ---------------------------------------------------------------- 主流程

    def begin(self, *, order_id: str, action_type: str,
              payload: Dict[str, Any]) -> IdempotencyRecord:
        """开始一次受保护的写操作。

        * 没有记录 -> 新建（`in_progress`），调用方继续执行；
        * 有记录且指纹相同 -> 返回它；若已完成，`replayed` 为 True，
          调用方**不要**再调下游；
        * 有记录但指纹不同 -> 抛 `IdempotencyConflict`。
        """
        key = build_key(order_id, action_type)
        plan_id = build_plan_id(order_id, action_type)
        fingerprint = request_hash(payload)

        existing = self.get(key)

        if existing is None:
            record = IdempotencyRecord(
                key=key, plan_id=plan_id, request_hash=fingerprint,
                status="in_progress", created_at=_now(),
            )
            self._put(record)
            logger.info(f"幂等记账：新建 {key}（plan_id={plan_id}）")
            return record

        if existing.request_hash != fingerprint:
            raise IdempotencyConflict(key, existing.request_hash, fingerprint)

        logger.info(
            f"幂等记账：命中 {key}（状态 {existing.status}，plan_id={existing.plan_id}）"
        )
        return existing

    def complete(self, key: str, result: Dict[str, Any]) -> IdempotencyRecord:
        """记录执行结果。之后任何同键请求都会被短路成这个结果。"""
        record = self.get(key)
        if record is None:
            # 正常路径不会走到这里（begin 一定先建）。真走到了说明记账丢了，
            # 补一条比直接抛异常好：钱已经花出去了，结果必须留下痕迹。
            logger.warning(f"幂等记账：完成时找不到 {key} 的记录，补建一条")
            record = IdempotencyRecord(key=key, plan_id="", request_hash="")
        record.status = "done"
        record.result = dict(result or {})
        self._put(record)
        logger.info(f"幂等记账：完成 {key} -> {record.result.get('refund_id') or '无单号'}")
        return record

    def fail(self, key: str, message: str) -> None:
        """标记失败。失败**不**写 done，下次请求仍然会真的重试。"""
        record = self.get(key)
        if record is None:
            return
        record.status = "failed"
        record.result = {"error": message}
        self._put(record)

    # ---------------------------------------------------------------- 审批去重

    def record_approval(self, *, thread_id: str, plan_id: str,
                        decision: str, operator: str = "") -> bool:
        """记录人工审批结论。**首次写入生效**，之后的重复写入被忽略。

        HITL 恢复时审批节点本身也在重放，所以它必须和退款节点一样是幂等的：
        第一次批准是批准，第二遍重放不应该产生第二条审批记录。
        返回 True 表示这次写入是首次（真的记了一笔）。
        """
        key = f"{thread_id}:{plan_id}"
        if self.get_approval(key) is not None:
            logger.info(f"审批记录已存在，忽略重放：{key}")
            return False

        try:
            self.store.put(APPROVAL_NAMESPACE, key, {
                "thread_id": thread_id,
                "plan_id": plan_id,
                "decision": decision,
                "operator": operator,
                "created_at": _now(),
            })
        except Exception as e:  # pragma: no cover
            logger.error(f"审批记录写入失败：{e}")
            return False
        return True

    def get_approval(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            item = self.store.get(APPROVAL_NAMESPACE, key)
        except Exception:  # pragma: no cover
            return None
        return dict(item.value) if item else None
