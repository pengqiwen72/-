"""
端口工厂：按 INTEGRATION_MODE 与 TOOL_GATEWAY 产出具体实现。

两个开关是**正交**的，各管一件事：

    INTEGRATION_MODE  决定「背后是谁」： mock（读种子） | http（真实业务系统）
    TOOL_GATEWAY      决定「怎么调」：   direct（进程内直调） | mcp（经 MCP 网关）

于是有四条组合：

    mock + direct  ← 默认，git clone 后零配置可跑
    mock + mcp     ← demo 用来证明「换调用路径结果不变」
    http + direct  ← 进程内直连生产系统
    http + mcp     ← 推荐的生产形态：工具层独立部署、单独限流与审计

两者相乘而不是合并成一个枚举，是因为它们回答的是不同的问题 ——
把「调哪个系统」和「怎么调它」糅成一个开关，就没法表达
「生产上走网关、本地调试直连」这种真实需求了。

七路端口（通知 / 日程 / 订单 / 物流 / 库存 / 退款 / 工单）共用一张配置表，
新增一路只需要在 `_PORT_TABLE` 里加一行 —— 逐个抄一遍构造逻辑，
是新增端口时最容易漏改其中一处的地方。
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Tuple

from integrations.ports import IntegrationError
from utils.agent_logger import get_logger

logger = get_logger("integration_factory")


def integration_mode() -> str:
    return (os.getenv("INTEGRATION_MODE") or "mock").strip().lower()


def gateway_mode() -> str:
    """工具调用路径：`direct`（默认，进程内直调）或 `mcp`（经 MCP 网关）。"""
    mode = (os.getenv("TOOL_GATEWAY") or "direct").strip().lower()
    return mode if mode in {"direct", "mcp"} else "direct"


def _use_http() -> bool:
    return integration_mode() in {"http", "live", "real"}


def _use_mcp() -> bool:
    return gateway_mode() == "mcp"


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

def _ensure_mcp_sdk() -> None:
    """MCP 模式下先确认 SDK 在位。

    不做这一步的话，缺 SDK 要等到第一次真正调工具才炸，报错还埋在
    bridge 的事件循环线程里 —— 启动期一句「没装 mcp」比运行期一句
    「工具调用失败」好排查得多。
    """
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        from integrations.mcp.bridge import SDK_HINT

        raise IntegrationError(SDK_HINT) from e


#: 动作名 -> (中文标签, http 类名, mock 模块名, mock 类名, mcp 类名)
_PORT_TABLE: dict[str, Tuple[str, str, str, str, str]] = {
    "order":     ("订单", "HttpOrderPort",     "order_mock",      "MockOrderPort",     "McpOrderPort"),
    "logistics": ("物流", "HttpLogisticsPort", "logistics_mock",  "MockLogisticsPort", "McpLogisticsPort"),
    "inventory": ("库存", "HttpInventoryPort", "inventory_mock",  "MockInventoryPort", "McpInventoryPort"),
    "refund":    ("退款", "HttpRefundPort",    "after_sale_mock", "MockRefundPort",    "McpRefundPort"),
    "handoff":   ("工单", "HttpHandoffPort",   "after_sale_mock", "MockHandoffPort",   "McpHandoffPort"),
}


def _business_port(action: str):
    """按 INTEGRATION_MODE × TOOL_GATEWAY 构造一个业务端口。"""
    label, http_cls, mock_module, mock_cls, mcp_cls = _PORT_TABLE[action]

    if _use_mcp():
        _ensure_mcp_sdk()

        from integrations.mcp import ports as mcp_ports

        logger.info(f"{label}端口：mcp（经 MCP 网关，背后是 {integration_mode()}）")
        return getattr(mcp_ports, mcp_cls)()

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
    """清空端口缓存（测试、以及切换环境变量时用）。

    ⚠️ 必须连 MCP 会话一起关掉。会话是**进程级**的，它的环境变量在
    子进程启动那一刻就固定了；只清端口缓存而留着旧会话，会让新注入的
    `MOCK_FAIL_ACTIONS` / `INTEGRATION_MODE` 送不到网关进程里去 ——
    表面上看端口是新建的，实际还在用旧环境的老子进程。
    """
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

    # 看「bridge 有没有被 import 过」而不是「当前是不是 mcp 模式」：
    # 环境变量在重置前后可能不同，而已经拉起来的子进程必须关掉，
    # 否则它会带着旧环境一直活着。没导入过就没有会话，也就没什么要关的。
    bridge = sys.modules.get("integrations.mcp.bridge")
    if bridge is not None:
        bridge.reset_session()
