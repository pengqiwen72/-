"""
物料 / 供应商 / 仓库 / 库存 / 采购 / 收货 / 附件 / 工单的真实实现（HTTP）。

只有 `INTEGRATION_MODE=http` 时才会走到这里，默认的 mock 路径完全不 import 本模块。

三件事值得说明
--------------
1. **本模块只负责「把上游返回原样交出来」**，不做任何字段映射。上游报文进来是
   `matCode` / `orderStatus` / `totalAmt`(分)，出去还是它们；归一交给
   `integrations/normalize.py`。这样归一规则只有一份，mock 和 http 两条路径
   走的是同一套映射，不会出现「离线测通了、连上真实系统字段全空」。
   例外是**写路径的请求体**：内部蛇形字段换上游报文由这一层做（见各 submit_*）。

2. **失败一律转成 `IntegrationError`**，不往上抛 `requests` 的异常类型。
   否则上层节点就得 import requests 才能 catch，端口抽象白做了。

3. **超时必须显式设置**。没有超时的外部调用在装载链路里是致命的：一个卡住的
   库存接口会把整轮请求拖到超时为止。默认 3 秒，可配。

连接池与熔断
------------
* 进程级共享 `requests.Session` + `HTTPAdapter`（pool_connections=8,
  pool_maxsize=16）：一轮装载要请求多个接口，每次重新握手是纯浪费；
  keep-alive 复用后同一 host 的连接不再重建。
* `request()` 经 `guarded(f"http:{host}", ...)` 走一层**按上游地址计**的熔断：
  端口那层管「这个业务能力坏了」，这层管「这个地址坏了」—— 业务层先短路，
  传输层兜住任何绕过端口的裸调用。两层名字空间不同，互不重复计数。

环境变量
--------
    ERP_BASE_URL / ERP_TOKEN             ERP（物料主数据、采购订单）
    SRM_BASE_URL / SRM_TOKEN             SRM（供应商档案、报价）
    WMS_BASE_URL / WMS_TOKEN             WMS（仓库、库存、收货）
    TICKET_BASE_URL / TICKET_TOKEN       服务台（转人工工单）
    OSS_BASE_URL / OSS_TOKEN             对象存储（质检报告归档）
    HTTP_TIMEOUT_SECONDS                 默认 3
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from integrations.ports import (
    FilePort,
    HandoffPort,
    IntegrationError,
    InventoryPort,
    MaterialPort,
    PurchasePort,
    ReceiptPort,
    SupplierPort,
    WarehousePort,
)
from integrations.resilience import guarded
from utils.agent_logger import get_logger

logger = get_logger("business_http")

DEFAULT_TIMEOUT = 3.0

#: 连接池大小：够覆盖「装载 + 子图并行取数」的并发面，又不至于把连接数开得离谱
_POOL_SIZE = 8
_POOL_MAX = 16


def _timeout() -> float:
    raw = (os.getenv("HTTP_TIMEOUT_SECONDS") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


def _host(base_url: str) -> str:
    """熔断器名：按上游地址（权限到 host 一级）计。"""
    return urlparse(base_url).netloc or base_url


def _cents(value: Any) -> int:
    """元 -> 分（下游请求体里的金额字段上游一律收分）。"""
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


# ==================== 连接池 ====================

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    """进程级共享的 HTTP 会话（带连接池）。"""
    global _session
    with _session_lock:
        if _session is None:
            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=_POOL_SIZE, pool_maxsize=_POOL_MAX)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            _session = session
        return _session


def reset_http_session() -> None:
    """关闭连接池（测试与 `factory.reset_ports()` 用）。"""
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
            _session = None


# ==================== 公共客户端 ====================

class _HttpClient:
    """极薄的 HTTP 封装：拼 URL、带鉴权、超时、错误归一、按地址熔断。

    刻意不引第三方 SDK：不同客户的 ERP / SRM / WMS 千差万别，能复用的只有
    「怎么发一个带 token 的 GET/POST 并把错误翻译成 IntegrationError」这一层。
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
        url = f"{self.base_url}{path}"
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", _timeout())
        return guarded(f"http:{_host(self.base_url)}", lambda: self._send(method, url, **kwargs))

    @staticmethod
    def _send(method: str, url: str, **kwargs) -> Dict[str, Any]:
        try:
            response = get_session().request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            # 网络异常、超时、非 2xx、非 JSON —— 全部归成一种，上层只需要 catch 一种
            raise IntegrationError(f"调用上游失败 {method} {url}：{e}") from e


# ==================== ERP：物料主数据 ====================

class HttpMaterialPort(_HttpClient, MaterialPort):
    base_url_env = "ERP_BASE_URL"
    token_env = "ERP_TOKEN"

    def list_materials(self) -> List[Dict[str, Any]]:
        payload = self.request("GET", "/api/material/list")
        return list(payload.get("data") or [])

    def get_material(self, material_code: str) -> Optional[Dict[str, Any]]:
        if not (material_code or "").strip():
            return None
        payload = self.request("GET", "/api/material/get", params={"matCode": material_code})
        # 业务层「查不到」是正常返回，不是异常：转成 None，与契约一致
        return payload.get("data") or None

    def search_materials(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not (keyword or "").strip():
            return []
        payload = self.request(
            "GET", "/api/material/search", params={"keyword": keyword, "limit": limit}
        )
        return list(payload.get("data") or [])

    def list_substitutes(self, material_code: str) -> List[Dict[str, Any]]:
        if not (material_code or "").strip():
            return []
        payload = self.request(
            "GET", "/api/material/substitutes", params={"matCode": material_code}
        )
        return list(payload.get("data") or [])


# ==================== SRM：供应商与报价 ====================

class HttpSupplierPort(_HttpClient, SupplierPort):
    base_url_env = "SRM_BASE_URL"
    token_env = "SRM_TOKEN"

    def list_suppliers(self) -> List[Dict[str, Any]]:
        payload = self.request("GET", "/api/supplier/list")
        return list(payload.get("data") or [])

    def get_supplier(self, supplier_code: str) -> Optional[Dict[str, Any]]:
        if not (supplier_code or "").strip():
            return None
        payload = self.request("GET", "/api/supplier/get", params={"supCode": supplier_code})
        return payload.get("data") or None

    def list_supplier_quotes(self, material_code: str) -> List[Dict[str, Any]]:
        if not (material_code or "").strip():
            return []
        payload = self.request("GET", "/api/quote/list", params={"matCode": material_code})
        return list(payload.get("data") or [])


# ==================== WMS：仓库 / 库存 / 收货 ====================

class HttpWarehousePort(_HttpClient, WarehousePort):
    base_url_env = "WMS_BASE_URL"
    token_env = "WMS_TOKEN"

    def list_warehouses(self) -> List[Dict[str, Any]]:
        payload = self.request("GET", "/api/warehouse/list")
        return list(payload.get("data") or [])


class HttpInventoryPort(_HttpClient, InventoryPort):
    base_url_env = "WMS_BASE_URL"
    token_env = "WMS_TOKEN"

    def list_stock(self) -> List[Dict[str, Any]]:
        payload = self.request("GET", "/api/stock/list")
        return list(payload.get("data") or [])

    def list_stock_by_material(self, material_code: str) -> List[Dict[str, Any]]:
        if not (material_code or "").strip():
            return []
        payload = self.request("GET", "/api/stock/by-material", params={"matCode": material_code})
        return list(payload.get("data") or [])

    def list_stock_by_warehouse(self, warehouse_code: str) -> List[Dict[str, Any]]:
        if not (warehouse_code or "").strip():
            return []
        payload = self.request("GET", "/api/stock/by-warehouse", params={"whCode": warehouse_code})
        return list(payload.get("data") or [])


class HttpReceiptPort(_HttpClient, ReceiptPort):
    base_url_env = "WMS_BASE_URL"
    token_env = "WMS_TOKEN"

    def submit_receipt(self, request: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "poNo": request.get("po_no", ""),
            "qtyActual": int(request.get("qty_received") or 0),
            "qtyBad": int(request.get("qty_defect") or 0),
            "badType": request.get("defect_type", ""),
            "disposal": request.get("disposition", ""),
            "whCode": request.get("warehouse_code", ""),
            "checker": request.get("inspector", ""),
            # 幂等键必须透传给上游（与采购下单同一套约定）
            "idempotencyKey": request.get("idempotency_key", ""),
        }
        payload = self.request("POST", "/api/receipt/submit", json=body)
        data = payload.get("data")
        if not data:
            raise IntegrationError(f"收货登记接口未返回数据：{payload}")
        return data

    def get_receipt(self, receipt_no: str) -> Optional[Dict[str, Any]]:
        if not (receipt_no or "").strip():
            return None
        payload = self.request("GET", "/api/receipt/get", params={"rcvNo": receipt_no})
        return payload.get("data") or None


# ==================== ERP：采购订单 ====================

class HttpPurchasePort(_HttpClient, PurchasePort):
    base_url_env = "ERP_BASE_URL"
    token_env = "ERP_TOKEN"

    def create_purchase_order(self, request: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "prNo": request.get("request_id", ""),
            "matCode": request.get("material_code", ""),
            "qty": request.get("qty", 0),
            "unit": request.get("unit", ""),
            "unitPrice": _cents(request.get("unit_price")),
            "totalAmt": _cents(request.get("amount")),
            "supCode": request.get("supplier_code", ""),
            "destWh": request.get("target_warehouse", ""),
            "operatorId": request.get("operator_id", ""),
            # 幂等键必须透传给上游：我们这层挡住了重复调用，但网络重试、
            # 并发请求这些情况只有上游自己也幂等才兜得住
            "idempotencyKey": request.get("idempotency_key", ""),
        }
        payload = self.request("POST", "/openapi/purchase/create", json=body)
        data = payload.get("data")
        if not data:
            raise IntegrationError(f"采购下单接口未返回数据：{payload}")
        return data

    def get_purchase_order(self, po_no: str) -> Optional[Dict[str, Any]]:
        if not (po_no or "").strip():
            return None
        payload = self.request("GET", "/openapi/purchase/get", params={"poNo": po_no})
        return payload.get("data") or None

    def list_purchase_orders(self) -> List[Dict[str, Any]]:
        payload = self.request("GET", "/openapi/purchase/list")
        return list(payload.get("data") or [])


# ==================== 附件 / 工单 ====================

class HttpFilePort(_HttpClient, FilePort):
    base_url_env = "OSS_BASE_URL"
    token_env = "OSS_TOKEN"

    def put_object(self, name: str, content: str,
                   content_type: str = "text/plain") -> Dict[str, Any]:
        payload = self.request("POST", "/api/object/put", json={
            "name": name,
            "content": content,
            "contentType": content_type,
        })
        data = payload.get("data")
        if not data:
            raise IntegrationError(f"对象存储接口未返回数据：{payload}")
        return data


class HttpHandoffPort(_HttpClient, HandoffPort):
    base_url_env = "TICKET_BASE_URL"
    token_env = "TICKET_TOKEN"

    def create_ticket(self, request: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "reason": request.get("reason", ""),
            "summary": request.get("summary", ""),
            "priority": request.get("priority", ""),
            "threadId": request.get("thread_id", ""),
            "operatorId": request.get("operator_id", ""),
            "idempotencyKey": request.get("idempotency_key", ""),
        }
        payload = self.request("POST", "/openapi/ticket/create", json=body)
        data = payload.get("data")
        if not data:
            raise IntegrationError(f"工单接口未返回数据：{payload}")
        return data
