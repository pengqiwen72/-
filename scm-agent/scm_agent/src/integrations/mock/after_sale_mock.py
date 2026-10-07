"""
退款 / 转人工工单 mock。

关于幂等，这里有一个刻意的取舍：**MockRefundPort 每次都真的新建一条退款单**，
它自己不做去重。去重是 `integrations/idempotency.IdempotencyGuard` 的职责。

为什么不让 mock 也顺手去个重，显得更「安全」
--------------------------------------------
因为那样 demo 就说不清了：重复提交没有产生第二笔退款，到底是我们的幂等生效了，
还是上游本来就是幂等的？把去重**只**放在我们这一层，演示时重复提交一次就能看到
「命中幂等，未重复发起」——如果哪天 guard 被删掉，立刻会看到两条退款记录，
这个保护是真的在起作用。

真实系统里上游通常也有自己的幂等键，两层都做才是常态；但在 demo 里，
把机制暴露在能被验证的那一层更有价值。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from langgraph.store.base import BaseStore

from integrations.mock import chaos
from integrations.ports import HandoffPort, RefundPort
from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("after_sale_mock")

REFUND_NAMESPACE = ("refund",)
TICKET_NAMESPACE = ("ticket",)


class _StoreBacked:
    """把记录落到 LangGraph Store：demo 里能查，后台能看到，且完全离线。"""

    def __init__(self, store: Optional[BaseStore] = None):
        self._store = store

    @property
    def store(self) -> BaseStore:
        if self._store is None:
            self._store = get_fallback_store()
        return self._store


class MockRefundPort(_StoreBacked, RefundPort):
    """退款执行（写操作）。"""

    def submit_refund(self, request: Dict[str, Any]) -> Dict[str, Any]:
        chaos.apply("refund")

        request = request or {}
        refund_id = f"RF-{uuid.uuid4().hex[:10].upper()}"
        record = {
            "refund_id": refund_id,
            "order_id": request.get("order_id", ""),
            "plan_id": request.get("plan_id", ""),
            "amount": round(float(request.get("amount", 0) or 0), 2),
            "refund_type": request.get("refund_type", ""),
            "reason": request.get("reason", ""),
            "operator": request.get("operator", ""),
            # 上游也留一份调用方的幂等键，便于线上对账时两边对齐
            "idempotency_key": request.get("idempotency_key", ""),
            "status": "accepted",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": "mock",
        }

        try:
            self.store.put(REFUND_NAMESPACE, refund_id, record)
        except Exception as e:  # pragma: no cover - 落库失败不应阻断业务
            logger.warning(f"退款记录落库失败（不影响提交）：{e}")

        logger.info(
            f"[MOCK 退款] 已受理 {refund_id}：订单 {record['order_id']} "
            f"{record['amount']:.2f} 元（{record['refund_type']}）"
        )
        return record

    def get_refund(self, refund_id: str) -> Optional[Dict[str, Any]]:
        chaos.apply("refund")

        if not refund_id:
            return None
        try:
            item = self.store.get(REFUND_NAMESPACE, refund_id)
        except Exception as e:  # pragma: no cover
            logger.warning(f"读取退款记录失败：{e}")
            return None
        return dict(item.value) if item else None


class MockHandoffPort(_StoreBacked, HandoffPort):
    """转人工工单。"""

    def create_ticket(self, request: Dict[str, Any]) -> Dict[str, Any]:
        chaos.apply("handoff")

        request = request or {}
        ticket_id = f"TK-{uuid.uuid4().hex[:10].upper()}"
        record = {
            "ticket_id": ticket_id,
            "order_id": request.get("order_id", ""),
            "external_id": request.get("external_id", ""),
            "assignee": request.get("assignee", ""),
            "title": request.get("title", ""),
            "detail": request.get("detail", ""),
            "priority": request.get("priority", "normal"),
            "status": "open",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": "mock",
        }

        try:
            self.store.put(TICKET_NAMESPACE, ticket_id, record)
        except Exception as e:  # pragma: no cover
            logger.warning(f"工单落库失败（不影响创建）：{e}")

        logger.info(f"[MOCK 工单] 已创建 {ticket_id}：{record['title']}")
        return record
