"""业务系统集成层：端口定义 + mock/http/mcp 适配器。"""

from integrations.ports import (  # noqa: F401
    FilePort,
    HandoffPort,
    IntegrationError,
    InventoryPort,
    MaterialNotFound,
    MaterialPort,
    PurchaseOrderNotFound,
    PurchasePort,
    ReceiptNotFound,
    ReceiptPort,
    SupplierNotFound,
    SupplierPort,
    WarehousePort,
)

__all__ = [
    "FilePort",
    "HandoffPort",
    "IntegrationError",
    "InventoryPort",
    "MaterialNotFound",
    "MaterialPort",
    "PurchaseOrderNotFound",
    "PurchasePort",
    "ReceiptNotFound",
    "ReceiptPort",
    "SupplierNotFound",
    "SupplierPort",
    "WarehousePort",
]
