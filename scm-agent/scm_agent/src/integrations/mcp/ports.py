"""
MCP 网关端口：把业务调用从「进程内函数调用」换成「跨进程协议调用」。

这是同一批 Protocol 的**第三套实现**：

    mock 实现  ->  读 data/seed/*.json，离线零依赖
    http 实现  ->  调真实业务系统（ERP / SRM / WMS / 服务台 / 对象存储）
    mcp 实现   ->  经 MCP stdio 网关调上面两者之一

三套实现的意义不在于「多写了一个适配器」，而在于证明端口抽象是真的：
节点代码一行没改，`TOOL_GATEWAY=direct|mcp` 一换，同一批场景就跑在不同的
调用路径上。如果当初节点里直接 `requests.get(...)`，这个开关根本无从做起。

适配器里**刻意不加任何业务判断**
--------------------------------
返回空值还是抛异常、要不要提前校验入参，全部交给网关那一侧的真实实现去决定 ——
在适配器里多加一个 `if not po_no: return None`，就等于在三套实现之间凭空
造出第四套行为，而它只在这个开关打开时才生效。这种「只在某条路径上存在的规则」
是最难排查的一类不一致。

工具面共 18 个（见根目录 `mcp_server/server.py`）。这里只做参数搬运：
工具名与端口方法一一对应，参数名就是上面的 snake_case 口径。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from integrations.mcp.bridge import get_session
from integrations.mcp.envelope import unwrap
from integrations.ports import (
    HandoffPort,
    IntegrationError,
    InventoryPort,
    MaterialPort,
    PurchasePort,
    ReceiptPort,
    SupplierPort,
    WarehousePort,
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


# ==================== 物料 ====================

class McpMaterialPort(_McpPort, MaterialPort):
    label = "物料"

    def list_materials(self) -> List[Dict[str, Any]]:
        return self._invoke("list_materials", {}) or []

    def get_material(self, material_code: str) -> Optional[Dict[str, Any]]:
        # 查不到返回 None 而不是抛异常：这是物料端口的契约
        # （见 ports.MaterialPort），与 mock / http 两套实现保持一致
        return self._invoke("get_material", {"material_code": material_code})

    def search_materials(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self._invoke(
            "search_materials", {"keyword": keyword, "limit": limit}
        ) or []

    def list_substitutes(self, material_code: str) -> List[Dict[str, Any]]:
        return self._invoke(
            "list_substitutes", {"material_code": material_code}
        ) or []


# ==================== 供应商 ====================

class McpSupplierPort(_McpPort, SupplierPort):
    label = "供应商"

    def list_suppliers(self) -> List[Dict[str, Any]]:
        return self._invoke("list_suppliers", {}) or []

    def get_supplier(self, supplier_code: str) -> Optional[Dict[str, Any]]:
        return self._invoke("get_supplier", {"supplier_code": supplier_code})

    def list_supplier_quotes(self, material_code: str) -> List[Dict[str, Any]]:
        return self._invoke(
            "list_supplier_quotes", {"material_code": material_code}
        ) or []


# ==================== 仓库 / 库存 ====================

class McpWarehousePort(_McpPort, WarehousePort):
    label = "仓库"

    def list_warehouses(self) -> List[Dict[str, Any]]:
        return self._invoke("list_warehouses", {}) or []


class McpInventoryPort(_McpPort, InventoryPort):
    label = "库存"

    def list_stock(self) -> List[Dict[str, Any]]:
        return self._invoke("list_stock", {}) or []

    def list_stock_by_material(self, material_code: str) -> List[Dict[str, Any]]:
        return self._invoke(
            "list_stock_by_material", {"material_code": material_code}
        ) or []

    def list_stock_by_warehouse(self, warehouse_code: str) -> List[Dict[str, Any]]:
        return self._invoke(
            "list_stock_by_warehouse", {"warehouse_code": warehouse_code}
        ) or []


# ==================== 采购 / 收货 / 工单（写链路） ====================

class McpPurchasePort(_McpPort, PurchasePort):
    label = "采购"

    def create_purchase_order(self, request: Dict[str, Any]) -> Dict[str, Any]:
        # 写链路的 not_found（引用的物料 / 供应商不存在）必须还原成异常，
        # 绝不能静默成空回执 —— 信封里带了原始异常类名，能还原出与直连
        # 完全一致的 *NotFound；`IntegrationError` 只是兜底。
        return self._invoke(
            "create_purchase_order", {"request": dict(request or {})},
            not_found_cls=IntegrationError,
        ) or {}

    def get_purchase_order(self, po_no: str) -> Optional[Dict[str, Any]]:
        return self._invoke("get_purchase_order", {"po_no": po_no})

    def list_purchase_orders(self) -> List[Dict[str, Any]]:
        return self._invoke("list_purchase_orders", {}) or []


class McpReceiptPort(_McpPort, ReceiptPort):
    label = "收货"

    def submit_receipt(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke(
            "submit_receipt", {"request": dict(request or {})},
            not_found_cls=IntegrationError,
        ) or {}

    def get_receipt(self, receipt_no: str) -> Optional[Dict[str, Any]]:
        return self._invoke("get_receipt", {"receipt_no": receipt_no})


class McpHandoffPort(_McpPort, HandoffPort):
    label = "工单"

    def create_ticket(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke(
            "create_ticket", {"request": dict(request or {})},
            not_found_cls=IntegrationError,
        ) or {}
