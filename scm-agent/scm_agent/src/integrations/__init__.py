"""业务系统集成层：端口定义 + mock/http 适配器。"""

from integrations.ports import (  # noqa: F401
    HandoffPort,
    InsufficientStock,
    IntegrationError,
    InventoryPort,
    LogisticsNotFound,
    LogisticsPort,
    NotifyPort,
    OrderNotFound,
    OrderPort,
    RefundPort,
    SchedulePort,
)

__all__ = [
    "HandoffPort",
    "InsufficientStock",
    "IntegrationError",
    "InventoryPort",
    "LogisticsNotFound",
    "LogisticsPort",
    "NotifyPort",
    "OrderNotFound",
    "OrderPort",
    "RefundPort",
    "SchedulePort",
]
