"""
物流轨迹 mock（第三方快递查询）。

「查不到轨迹」抛 `LogisticsNotFound` 而不是返回空 dict：这是**业务上的确定结论**
（该单还没有物流信息，比如还没出库），调用方据此给出「尚未发货」的答复；
它与「接口挂了」（`IntegrationError`）是两回事，必须能被上层区分开。
"""

from __future__ import annotations

from typing import Any, Dict

from integrations.mock import chaos, seed_source
from integrations.ports import LogisticsNotFound, LogisticsPort
from utils.agent_logger import get_logger

logger = get_logger("logistics_mock")


class MockLogisticsPort(LogisticsPort):
    """读 `data/seed/logistics.json`，按快递查询接口的字段形状返回。"""

    def get_trace(self, order_id: str) -> Dict[str, Any]:
        chaos.apply("logistics")

        key = (order_id or "").strip()
        trace = seed_source.logistics_raw().get(key)
        if not trace:
            logger.info(f"[MOCK 快递查询] 订单 {key} 暂无物流轨迹")
            raise LogisticsNotFound(f"订单 {key} 暂无物流轨迹")

        logger.debug(f"[MOCK 快递查询] 命中 {key}：{trace.get('state')}")
        return trace
