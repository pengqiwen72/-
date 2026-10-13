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

两者相乘而不是合并成一个枚举，是因为它们回答的是不同问题 ——
把「调哪个系统」和「怎么调它」糅成一个开关，就没法表达
「生产上走网关、本地调试直连」这种真实需求了。

八个端口（物料 / 供应商 / 仓库 / 库存 / 采购 / 收货 / 对象存储 / 工单）
共用一张配置表，新增一路只需要在 `_PORT_TABLE` 里加一行 —— 逐个抄一遍
构造逻辑，是新增端口时最容易漏改其中一处的地方。

构造出的每个端口都经 `resilience.wrap_port()` 套一层熔断代理（名字=动作名），
三种后端共用同一套熔断名 —— 这样 `TOOL_GATEWAY=direct|mcp` 的行为才逐字段
一致，而不是「直连会熔断、走网关不会」这种只在某条路径上存在的差异。
"""

from __future__ import annotations

import importlib
import os
import sys
from functools import lru_cache
from typing import Optional, Tuple

from integrations.ports import IntegrationError
from integrations.resilience import reset_breakers, wrap_port
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
#:
#: mcp_cls 为 None 表示**该端口不进 MCP 网关**（目前只有对象存储）：
#: 附件写入是字节落盘、没有业务判断，网关的「薄包装 + 信封」没有增益，
#: 而且唯一的调用方在网关服务端内部（mock 收货落质检报告），再穿一次
#: 网关只是自己调自己。见 `integrations/ports.py::FilePort`。
_PORT_TABLE: dict[str, Tuple[str, str, str, str, Optional[str]]] = {
    "material":  ("物料",     "HttpMaterialPort",  "masterdata_mock", "MockMaterialPort",  "McpMaterialPort"),
    "supplier":  ("供应商",   "HttpSupplierPort",  "masterdata_mock", "MockSupplierPort",  "McpSupplierPort"),
    "warehouse": ("仓库",     "HttpWarehousePort", "masterdata_mock", "MockWarehousePort", "McpWarehousePort"),
    "inventory": ("库存",     "HttpInventoryPort", "inventory_mock",  "MockInventoryPort", "McpInventoryPort"),
    "purchase":  ("采购",     "HttpPurchasePort",  "purchase_mock",   "MockPurchasePort",  "McpPurchasePort"),
    "receipt":   ("收货",     "HttpReceiptPort",   "receipt_mock",    "MockReceiptPort",   "McpReceiptPort"),
    "file":      ("对象存储", "HttpFilePort",      "file_mock",       "MockFilePort",      None),
    "handoff":   ("工单",     "HttpHandoffPort",   "handoff_mock",    "MockHandoffPort",   "McpHandoffPort"),
}


def _business_port(action: str):
    """按 INTEGRATION_MODE × TOOL_GATEWAY 构造一个业务端口，并套上熔断代理。"""
    label, http_cls, mock_module, mock_cls, mcp_cls = _PORT_TABLE[action]

    if _use_mcp() and mcp_cls:
        _ensure_mcp_sdk()

        from integrations.mcp import ports as mcp_ports

        logger.info(f"{label}端口：mcp（经 MCP 网关，背后是 {integration_mode()}）")
        impl = getattr(mcp_ports, mcp_cls)()
    elif _use_http():
        from integrations.http import business_http

        logger.info(f"{label}端口：http（真实业务系统）")
        impl = getattr(business_http, http_cls)()
    else:
        module = importlib.import_module(f"integrations.mock.{mock_module}")

        logger.debug(f"{label}端口：mock（读种子数据，零网络）")
        impl = getattr(module, mock_cls)()

    # 熔断名 = 动作名（与 MOCK_FAIL_ACTIONS 同一套词）。三种后端共用，
    # direct / mcp 的熔断行为才对齐。
    return wrap_port(action, impl)


@lru_cache(maxsize=1)
def get_material_port():
    """获取物料主数据端口（进程级单例）。"""
    return _business_port("material")


@lru_cache(maxsize=1)
def get_supplier_port():
    """获取供应商端口（进程级单例）。"""
    return _business_port("supplier")


@lru_cache(maxsize=1)
def get_warehouse_port():
    """获取仓库端口（进程级单例）。"""
    return _business_port("warehouse")


@lru_cache(maxsize=1)
def get_inventory_port():
    """获取库存端口（进程级单例）。"""
    return _business_port("inventory")


@lru_cache(maxsize=1)
def get_purchase_port():
    """获取采购端口（进程级单例）。"""
    return _business_port("purchase")


@lru_cache(maxsize=1)
def get_receipt_port():
    """获取收货端口（进程级单例）。"""
    return _business_port("receipt")


@lru_cache(maxsize=1)
def get_file_port():
    """获取对象存储端口（进程级单例；不进 MCP 网关，始终本地直连）。"""
    return _business_port("file")


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

    熔断器与 HTTP 连接池也一样：故障撤掉之后还留着 OPEN 的熔断器，
    下一个场景会先吃几次「无辜的快速失败」，「恢复」这件事就演不出来了。
    """
    for getter in (
        get_material_port,
        get_supplier_port,
        get_warehouse_port,
        get_inventory_port,
        get_purchase_port,
        get_receipt_port,
        get_file_port,
        get_handoff_port,
    ):
        getter.cache_clear()

    # 看「模块有没有被 import 过」而不是「当前是不是那个模式」：
    # 环境变量在重置前后可能不同，而已经拉起来的资源必须按现状关掉。
    # 没导入过就没有资源，也就没什么可清的。
    bridge = sys.modules.get("integrations.mcp.bridge")
    if bridge is not None:
        bridge.reset_session()

    business_http = sys.modules.get("integrations.http.business_http")
    if business_http is not None:
        business_http.reset_http_session()

    reset_breakers()
