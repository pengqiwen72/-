"""
MCP 网关端口：把业务调用从「进程内函数调用」换成「跨进程协议调用」。

这是同一批 Protocol 的**第三套实现**：

    mock 实现  ->  读 data/seed/*.json，离线零依赖
    http 实现  ->  调真实业务系统
    mcp 实现   ->  经 MCP stdio 网关调上面两者之一

三套实现的意义不在于「多写了一个适配器」，而在于证明端口抽象是真的：
节点代码一行没改，`TOOL_GATEWAY=direct|mcp` 一换，同一批场景就跑在不同的
调用路径上。如果当初节点里直接 `requests.get(...)`，这个开关根本无从做起。

适配器里**刻意不加任何业务判断**
--------------------------------
返回空值还是抛异常、要不要提前校验入参，全部交给网关那一侧的真实实现去决定 ——
在适配器里多加一个 `if not order_id: return None`，就等于在三套实现之间凭空
造出第四套行为，而它只在这个开关打开时才生效。这种「只在某条路径上存在的规则」
是最难排查的一类不一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from integrations.mcp.bridge import get_session
from integrations.mcp.envelope import unwrap
from integrations.ports import (
    HandoffPort,
    IntegrationError,
    InventoryPort,
    LogisticsNotFound,
    LogisticsPort,
    OrderPort,
    RefundPort,
)
from utils.agent_logger import get_logger

logger = get_logger("mcp_ports")


class _McpPort:
    """MCP 网关端口的公共部分：所有方法都收敛到 `_invoke` 一处。"""

    #: 日志用的中文名
    label = "业务"

    def _invoke(self, tool: str, arguments: Dict[str, Any],
                not_found_cls: Optional[Type[IntegrationError]] = None) -> Any:
        logger.debug(f"[MCP {self.label}] {tool}({arguments})")
        envelope = get_session().call_tool(tool, arguments)
        return unwrap(envelope, tool=tool, not_found_cls=not_found_cls)


# ==================== 订单 ====================

class McpOrderPort(_McpPort, OrderPort):
    label = "订单"

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        # 查不到返回 None 而不是抛异常：这是订单端口的契约（见 ports.OrderPort），
        # 与 mock / http 两套实现保持一致
        return self._invoke("get_order", {"order_id": order_id})

    def list_orders_by_customer(self, customer_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self._invoke(
            "list_orders_by_customer", {"customer_id": customer_id, "limit": limit}
        ) or []

    def list_orders_by_range(self, start: str, end: str, limit: int = 500) -> List[Dict[str, Any]]:
        return self._invoke(
            "list_orders_by_range", {"start": start, "end": end, "limit": limit}
        ) or []


# ==================== 物流 ====================

class McpLogisticsPort(_McpPort, LogisticsPort):
    label = "物流"

    def get_trace(self, order_id: str) -> Dict[str, Any]:
        # 「没有轨迹」是业务结论不是故障，所以这里把 not_found 码还原成
        # LogisticsNotFound，让上层能照常走「暂无物流信息」那条分支
        return self._invoke(
            "get_logistics", {"order_id": order_id}, not_found_cls=LogisticsNotFound
        )


# ==================== 库存 ====================

class McpInventoryPort(_McpPort, InventoryPort):
    label = "库存"

    def get_stock(self, sku_ids: List[str], warehouse: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._invoke(
            "get_stock", {"sku_ids": list(sku_ids or []), "warehouse": warehouse}
        ) or []


# ==================== 退款 / 工单 ====================

class McpRefundPort(_McpPort, RefundPort):
    label = "退款"

    def submit_refund(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke("submit_refund", {"request": dict(request or {})}) or {}

    def get_refund(self, refund_id: str) -> Optional[Dict[str, Any]]:
        return self._invoke("get_refund", {"refund_id": refund_id})


class McpHandoffPort(_McpPort, HandoffPort):
    label = "工单"

    def create_ticket(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke("create_ticket", {"request": dict(request or {})}) or {}
