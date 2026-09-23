"""
业务系统端口（Port）。

用 Protocol 定义接口，把「业务能力」与「调用哪个外部系统」解耦：

    mock 实现 -> 读 data/seed/*.json，零依赖、零网络，demo 默认走这条
    http 实现 -> 调真实业务系统，生产走这条

这样 Agent 的节点代码不需要知道背后是企业微信、自研 OMS 还是别的什么，
换供应商只改一个适配器 + 一个环境变量。

⚠️ 所有外部调用必须经由端口，禁止在节点里直接 new 客户端。
   历史实现里 `sub_tag_node` 直接构造 `WxWorkAPI()`，导致离线跑 demo 时
   会真的向企业微信发起请求（还会因为 IP 白名单失败），既慢又违反「离线」承诺。
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class NotifyPort(Protocol):
    """消息通知（审批提醒、结果推送）。"""

    def notify(self, user_id: str, content: str, title: str = "") -> bool: ...


@runtime_checkable
class SchedulePort(Protocol):
    """日程/跟进任务创建。"""

    def create_schedule(
        self, user_id: str, title: str, start_time: str, duration_minutes: int = 30
    ) -> bool: ...


@runtime_checkable
class OrderPort(Protocol):
    """订单主数据。"""

    def get_order(self, order_id: str) -> Optional[dict]: ...

    def list_orders_by_customer(self, customer_id: str, limit: int = 20) -> List[dict]: ...


@runtime_checkable
class LogisticsPort(Protocol):
    """物流轨迹。"""

    def get_trace(self, order_id: str) -> dict: ...


@runtime_checkable
class InventoryPort(Protocol):
    """库存。"""

    def get_stock(self, sku_ids: List[str], warehouse: Optional[str] = None) -> List[dict]: ...


@runtime_checkable
class RefundPort(Protocol):
    """退款执行（写操作，必须携带幂等键）。"""

    def submit_refund(self, request: dict) -> dict: ...

    def get_refund(self, refund_id: str) -> Optional[dict]: ...


@runtime_checkable
class HandoffPort(Protocol):
    """转人工工单。"""

    def create_ticket(self, request: dict) -> dict: ...


class IntegrationError(RuntimeError):
    """业务系统调用失败。"""


class OrderNotFound(IntegrationError):
    """订单不存在。"""


class LogisticsNotFound(IntegrationError):
    """未查到物流信息。"""


class InsufficientStock(IntegrationError):
    """库存不足。"""
