"""
订单 / 物流 / 库存 / 退款 / 工单的真实实现（HTTP）。

只有 `INTEGRATION_MODE=http` 时才会走到这里，默认的 mock 路径完全不 import 本模块。

三件事值得说明
--------------
1. **本模块只负责「把上游返回原样交出来」**，不做任何字段映射。上游报文进来是
   `orderNo` / `tradeStatus` / `payFee`，出去还是它们；归一交给
   `integrations/normalize.py`。这样归一规则只有一份，mock 和 http 两条路径
   走的是同一套映射，不会出现「离线测通了、连上真实系统字段全空」。

2. **失败一律转成 `IntegrationError`**，不往上抛 `requests` 的异常类型。
   否则上层节点就得 import requests 才能 catch，端口抽象白做了。

3. **超时必须显式设置**。没有超时的外部调用在客服链路里是致命的：一个卡住的
   库存接口会把整个订单查询拖到客户挂断为止。默认 3 秒，可配。

环境变量
--------
    OMS_BASE_URL / OMS_TOKEN          订单中台
    LOGISTICS_BASE_URL / LOGISTICS_KEY 快递查询
    WMS_BASE_URL / WMS_TOKEN          仓储
    HTTP_TIMEOUT_SECONDS              默认 3
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

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

logger = get_logger("business_http")

DEFAULT_TIMEOUT = 3.0


def _timeout() -> float:
    raw = (os.getenv("HTTP_TIMEOUT_SECONDS") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


class _HttpClient:
    """极薄的 HTTP 封装：拼 URL、带鉴权、超时、错误归一。

    刻意不引第三方 SDK：不同客户的订单中台千差万别，能复用的只有「怎么发一个
    带 token 的 GET/POST 并把错误翻译成 IntegrationError」这一层。
    """

    #: 读取 base url 的环境变量名（子类覆盖）
    base_url_env = ""
    #: 读取鉴权 token 的环境变量名（子类覆盖）
    token_env = ""

    def __init__(self, base_url: str = "", token: str = ""):
        self._base_url = base_url or (os.getenv(self.base_url_env) or "").rstrip("/")
        self._token = token or (os.getenv(self.token_env) or "")

    @property
    def base_url(self) -> str:
        if not self._base_url:
            raise IntegrationError(
                f"未配置 {self.base_url_env}，无法调用上游（当前 INTEGRATION_MODE=http）"
            )
        return self._base_url

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        import requests  # 延迟 import：离线路径不该依赖它

        url = f"{self.base_url}{path}"
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", _timeout())

        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            # 网络异常、超时、非 2xx、非 JSON —— 全部归成一种，上层只需要 catch 一种
            raise IntegrationError(f"调用上游失败 {method} {url}：{e}") from e


# ==================== 订单中台 ====================

class HttpOrderPort(_HttpClient, OrderPort):
    base_url_env = "OMS_BASE_URL"
    token_env = "OMS_TOKEN"

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        if not (order_id or "").strip():
            return None
        payload = self.request("GET", "/openapi/order/get", params={"orderNo": order_id})
        # 业务层「查不到」是正常返回，不是异常：转成 None，与契约一致
        return payload.get("data") or None

    def list_orders_by_customer(self, customer_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not (customer_id or "").strip():
            return []
        payload = self.request(
            "GET", "/openapi/order/list", params={"unionId": customer_id, "limit": limit}
        )
        return list(payload.get("data") or [])

    def list_orders_by_range(self, start: str, end: str, limit: int = 500) -> List[Dict[str, Any]]:
        payload = self.request(
            "GET", "/openapi/order/range",
            params={"startDate": start, "endDate": end, "limit": limit},
        )
        return list(payload.get("data") or [])


# ==================== 快递查询 ====================

class HttpLogisticsPort(_HttpClient, LogisticsPort):
    base_url_env = "LOGISTICS_BASE_URL"
    token_env = "LOGISTICS_KEY"

    def get_trace(self, order_id: str) -> Dict[str, Any]:
        payload = self.request("GET", "/poll/query.do", params={"orderNo": order_id})
        trace = payload.get("data")
        if not trace:
            raise LogisticsNotFound(f"订单 {order_id} 暂无物流轨迹")
        return trace


# ==================== 仓储 WMS ====================

class HttpInventoryPort(_HttpClient, InventoryPort):
    base_url_env = "WMS_BASE_URL"
    token_env = "WMS_TOKEN"

    def get_stock(self, sku_ids: List[str], warehouse: Optional[str] = None) -> List[Dict[str, Any]]:
        if not sku_ids:
            return []
        params: Dict[str, Any] = {"skuCode": ",".join(sku_ids)}
        if warehouse:
            params["warehouse"] = warehouse
        payload = self.request("GET", "/api/stock/query", params=params)
        return list(payload.get("data") or [])


# ==================== 退款 ====================

class HttpRefundPort(_HttpClient, RefundPort):
    base_url_env = "OMS_BASE_URL"
    token_env = "OMS_TOKEN"

    def submit_refund(self, request: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(request or {})
        # 幂等键必须透传给上游：我们这层挡住了重复调用，但网络重试、
        # 并发请求这些情况只有上游自己也幂等才兜得住
        body.setdefault("idempotencyKey", body.get("idempotency_key", ""))
        payload = self.request("POST", "/openapi/refund/submit", json=body)
        data = payload.get("data")
        if not data:
            raise IntegrationError(f"退款接口未返回数据：{payload}")
        return data

    def get_refund(self, refund_id: str) -> Optional[Dict[str, Any]]:
        if not (refund_id or "").strip():
            return None
        payload = self.request("GET", "/openapi/refund/get", params={"refundId": refund_id})
        return payload.get("data") or None


# ==================== 转人工工单 ====================

class HttpHandoffPort(_HttpClient, HandoffPort):
    base_url_env = "OMS_BASE_URL"
    token_env = "OMS_TOKEN"

    def create_ticket(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.request("POST", "/openapi/ticket/create", json=dict(request or {}))
        data = payload.get("data")
        if not data:
            raise IntegrationError(f"工单接口未返回数据：{payload}")
        return data
