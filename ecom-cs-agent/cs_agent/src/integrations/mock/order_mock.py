"""
订单主数据 mock（订单中台）。

返回的是**上游报文的形状**，不是内部标准结构 —— 理由见 `seed_source.py`。
调用方拿到之后必须走 `integrations.normalize`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# 东八区：区间边界要按业务时区的自然日切，不能用 UTC
_TZ_CHINA = timezone(timedelta(hours=8))

from integrations.mock import chaos, seed_source
from integrations.ports import OrderPort
from utils.agent_logger import get_logger

logger = get_logger("order_mock")


class MockOrderPort(OrderPort):
    """读 `data/seed/orders.json`，按订单中台的字段形状返回。"""

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        chaos.apply("order")

        key = (order_id or "").strip()
        if not key:
            return None

        for order in seed_source.orders_raw():
            if order["orderNo"] == key:
                logger.debug(f"[MOCK 订单中台] 命中 {key}")
                return order

        logger.info(f"[MOCK 订单中台] 未找到订单 {key}")
        return None

    def list_orders_by_customer(self, customer_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        chaos.apply("order")

        key = (customer_id or "").strip()
        if not key:
            return []

        hit = [o for o in seed_source.orders_raw() if o.get("_unionId") == key]
        # 按下单时间倒序：客户问「我的订单」时，先看最近一笔
        hit.sort(key=lambda o: o.get("gmtCreate", 0), reverse=True)
        logger.debug(f"[MOCK 订单中台] 客户 {key} 命中 {len(hit)} 笔订单")
        return hit[:limit]

    def list_orders_by_range(self, start: str, end: str, limit: int = 500) -> List[Dict[str, Any]]:
        chaos.apply("order")

        low = _day_start_ms(start)
        high = _day_end_ms(end)
        if low is None or high is None:
            logger.warning(f"[MOCK 订单中台] 区间参数无法解析：{start!r} ~ {end!r}")
            return []

        hit = [o for o in seed_source.orders_raw() if low <= o.get("gmtCreate", 0) <= high]
        hit.sort(key=lambda o: o.get("gmtCreate", 0))
        logger.debug(f"[MOCK 订单中台] 区间 {start}~{end} 命中 {len(hit)} 笔订单")
        return hit[:limit]


def _day_start_ms(day: str) -> Optional[int]:
    """`yyyy-MM-dd` -> 当天 00:00:00 的毫秒时间戳（东八区）。

    区间**含端点**：报表口径里「本周」是闭区间，边界那天算进去才符合直觉。
    """
    moment = _parse_day(day)
    return int(moment.timestamp() * 1000) if moment else None


def _day_end_ms(day: str) -> Optional[int]:
    moment = _parse_day(day)
    if moment is None:
        return None
    return int((moment + timedelta(days=1)).timestamp() * 1000) - 1


def _parse_day(day: str) -> Optional[datetime]:
    try:
        return datetime.strptime((day or "").strip(), "%Y-%m-%d").replace(tzinfo=_TZ_CHINA)
    except ValueError:
        return None
