"""
端口工厂：按 INTEGRATION_MODE 产出 mock 或 http 实现。

    INTEGRATION_MODE=mock（默认）-> 读种子数据，零网络零依赖
    INTEGRATION_MODE=http         -> 调真实业务系统

默认 mock 是刻意的：让 `git clone` 之后不配任何东西就能跑，
也让 demo 永远不会因为外部系统抖动而失败。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

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


def reset_ports() -> None:
    """清空端口缓存（测试用）。"""
    get_notify_port.cache_clear()
    get_schedule_port.cache_clear()
