"""
端口工厂：按 INTEGRATION_MODE 产出 mock 或 http 实现。

    INTEGRATION_MODE=mock（默认）-> 读种子数据，零网络零依赖
    INTEGRATION_MODE=http         -> 调真实业务系统

默认 mock 是刻意的：让 `git clone` 之后不配任何东西就能跑，
也让 demo 永远不会因为外部系统抖动而失败。

七路端口（通知 / 日程 / 订单 / 物流 / 库存 / 退款 / 工单）共用一张配置表，
新增一路只需要在 `_PORT_TABLE` 里加一行 —— 逐个抄一遍构造逻辑，
是新增端口时最容易漏改其中一处的地方。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Tuple

from utils.agent_logger import get_logger

logger = get_logger("integration_factory")


def integration_mode() -> str:
    return (os.getenv("INTEGRATION_MODE") or "mock").strip().lower()


def _use_http() -> bool:
    return integration_mode() in {"http", "live", "real"}


# ==================== 通知 / 日程 ====================

@lru_cache(maxsize=1)
def get_notify_port():
    """获取通知端口（进程级单例）。"""
    if _use_http():
        from integrations.http.notify_http import WxWorkNotifyPort

        logger.info("通知端口：企业微信（http）")
        return WxWorkNotifyPort()

    from integrations.mock.notify_mock import MockNotifyPort

    logger.debug("通知端口：mock（不发起网络请求）")
    return MockNotifyPort()


@lru_cache(maxsize=1)
def get_schedule_port():
    """获取日程端口（进程级单例）。"""
    if _use_http():
        from integrations.http.notify_http import WxWorkSchedulePort

        logger.info("日程端口：企业微信（http）")
        return WxWorkSchedulePort()

    from integrations.mock.notify_mock import MockSchedulePort

    logger.debug("日程端口：mock（不发起网络请求）")
    return MockSchedulePort()


# ==================== 订单 / 物流 / 库存 / 退款 / 工单 ====================

#: 动作名 -> (中文标签, http 实现类名, mock 模块名, mock 实现类名)
_PORT_TABLE: dict[str, Tuple[str, str, str, str]] = {
    "order":     ("订单", "HttpOrderPort",     "order_mock",      "MockOrderPort"),
    "logistics": ("物流", "HttpLogisticsPort", "logistics_mock",  "MockLogisticsPort"),
    "inventory": ("库存", "HttpInventoryPort", "inventory_mock",  "MockInventoryPort"),
    "refund":    ("退款", "HttpRefundPort",    "after_sale_mock", "MockRefundPort"),
    "handoff":   ("工单", "HttpHandoffPort",   "after_sale_mock", "MockHandoffPort"),
}


def _business_port(action: str):
    """按 INTEGRATION_MODE 构造一个业务端口。"""
    label, http_cls, mock_module, mock_cls = _PORT_TABLE[action]

    if _use_http():
        from integrations.http import business_http

        logger.info(f"{label}端口：http（真实业务系统）")
        return getattr(business_http, http_cls)()

    from integrations.mock import (
        after_sale_mock,
        inventory_mock,
        logistics_mock,
        order_mock,
    )

    logger.debug(f"{label}端口：mock（读种子数据，零网络）")
    modules = {
        "order_mock": order_mock,
        "logistics_mock": logistics_mock,
        "inventory_mock": inventory_mock,
        "after_sale_mock": after_sale_mock,
    }
    return getattr(modules[mock_module], mock_cls)()


@lru_cache(maxsize=1)
def get_order_port():
    """获取订单主数据端口（进程级单例）。"""
    return _business_port("order")


@lru_cache(maxsize=1)
def get_logistics_port():
    """获取物流轨迹端口（进程级单例）。"""
    return _business_port("logistics")


@lru_cache(maxsize=1)
def get_inventory_port():
    """获取库存端口（进程级单例）。"""
    return _business_port("inventory")


@lru_cache(maxsize=1)
def get_refund_port():
    """获取退款端口（进程级单例）。"""
    return _business_port("refund")


@lru_cache(maxsize=1)
def get_handoff_port():
    """获取转人工工单端口（进程级单例）。"""
    return _business_port("handoff")


def reset_ports() -> None:
    """清空端口缓存（测试用）。"""
    for getter in (
        get_notify_port,
        get_schedule_port,
        get_order_port,
        get_logistics_port,
        get_inventory_port,
        get_refund_port,
        get_handoff_port,
    ):
        getter.cache_clear()
