"""
转人工工单 mock（服务台系统）。

写路径惯例与采购 / 收货 mock 一致：真的建单、不做去重（幂等是守卫的职责）、
幂等键透传留痕。运行期工单落 `("ticket_runtime",)`，与种子的 `("ticket",)`
分开 —— 理由同 `purchase_mock`（种子是内部字段名，mock 写的是上游报文形状）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from integrations.mock import chaos
from integrations.ports import HandoffPort
from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("handoff_mock")

_TZ_CHINA = timezone(timedelta(hours=8))

_RUNTIME_NAMESPACE = ("ticket_runtime",)


class MockHandoffPort(HandoffPort):
    """建单即落 store，返回上游形状的工单回执（ticketNo 由服务台生成）。"""

    def create_ticket(self, request: Dict[str, Any]) -> Dict[str, Any]:
        chaos.apply("handoff")

        req = dict(request or {})
        now_ms = int(datetime.now(_TZ_CHINA).timestamp() * 1000)
        record: Dict[str, Any] = {
            "ticketNo": f"TK{datetime.now(_TZ_CHINA):%Y%m%d}{uuid.uuid4().hex[:5].upper()}",
            "ticketStatus": "open",
            "reason": str(req.get("reason", "")),
            "summary": str(req.get("summary", "")),
            "priority": str(req.get("priority") or "medium"),
            "threadId": str(req.get("thread_id", "")),
            "operatorId": str(req.get("operator_id", "")),
            "gmtCreate": now_ms,
            "idempotencyKey": str(req.get("idempotency_key", "")),
        }

        get_fallback_store().put(_RUNTIME_NAMESPACE, record["ticketNo"], record)
        logger.info(f"[MOCK 服务台] 已建转人工工单 {record['ticketNo']}（{record['reason'] or '未注明原因'}）")
        return record
