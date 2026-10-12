"""
采购订单 mock（ERP 采购模块），含写路径：下达采购订单。

写路径的惯例
------------------------------------------------------------
* 每次 `create_purchase_order` 都**真的**新建一张单，本端口**刻意不做去重**。
  去重是 `integrations/idempotency.IdempotencyGuard` 的职责 —— 把去重放在
  端口里，演示时重复提交一次就分不清「是端口不去重」还是「幂等守卫拦住了」；
  把去重只放在守卫那一层，重复提交一次就能看到「命中幂等，未重复下单」。
* 幂等键照单透传并留痕（`idempotencyKey`）：网络重试、并发请求这些情况
  只有上游自己也幂等才兜得住 —— 这是给真实系统的约定，mock 只负责如实模仿。

⚠️ 运行期下达的单落 `("po_runtime",)` **独立命名空间**，不写进 `("purchase_order",)`
-------------------------------------------------------------------------------
`("purchase_order",)` 里躺着 seed_loader 灌进去的种子（**内部字段名**），
而 mock 写回的是**上游报文形状**（poNo/totalAmt…）。两种口径混在同一个命名空间里，
读出来就得按字段名猜形状 —— 猜错的后果是静默的。运行期写自己的命名空间之后：
种子读 JSON（`seed_source`）、运行期读写 `("po_runtime",)`，两边形状各自干净，
merge 规则也简单（同单号以运行期为准，实际不会撞号）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from integrations.mock import chaos, seed_source
from integrations.ports import MaterialNotFound, PurchasePort, SupplierNotFound
from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("purchase_mock")

_TZ_CHINA = timezone(timedelta(hours=8))

#: 运行期下达的采购单（上游报文形状）。
_RUNTIME_NAMESPACE = ("po_runtime",)
_SEARCH_ALL = 1000


def _norm(code: str) -> str:
    return (code or "").strip().upper()


def _new_po_no() -> str:
    """上游生成单号的模仿：日期 + 随机尾号（真实系统里是自增序列）。"""
    return f"PO{datetime.now(_TZ_CHINA):%Y%m%d}{uuid.uuid4().hex[:5].upper()}"


def find_purchase_order(po_no: str) -> Optional[Dict[str, Any]]:
    """按单号查采购单（运行期优先，其次种子），**不带故障注入**。

    供本文件与收货 mock 共用：收货登记要反查采购单的物料 / 应收量 / 供应商，
    这条内部反查不该再走一遍 `chaos.apply("purchase")` —— 否则注入「采购模块
    故障」时收货登记也会跟着挂，故障语义就说不清了。
    """
    key = _norm(po_no)
    if not key:
        return None

    item = get_fallback_store().get(_RUNTIME_NAMESPACE, key)
    if item is not None and isinstance(item.value, dict):
        return dict(item.value)

    for po in seed_source.purchase_orders_raw():
        if po["poNo"] == key:
            return po
    return None


class MockPurchasePort(PurchasePort):
    """读种子 + 运行期单；写下单记录。"""

    def create_purchase_order(self, request: Dict[str, Any]) -> Dict[str, Any]:
        chaos.apply("purchase")

        req = dict(request or {})
        material_code = _norm(req.get("material_code", ""))
        supplier_code = _norm(req.get("supplier_code", ""))

        # 上游会拒绝引用不存在的主数据 —— mock 如实模仿，把「这次写没法完成」
        # 通过信封的 not_found 码交回调用方（而不是先落单再对不上账）
        if not any(m["matCode"] == material_code for m in seed_source.materials_raw()):
            raise MaterialNotFound(f"物料 {material_code or '(空)'} 不存在，无法下达采购订单")
        if supplier_code and not any(
            s["supCode"] == supplier_code for s in seed_source.suppliers_raw()
        ):
            raise SupplierNotFound(f"供应商 {supplier_code} 不存在，无法下达采购订单")

        now_ms = int(datetime.now(_TZ_CHINA).timestamp() * 1000)
        record: Dict[str, Any] = {
            "poNo": _new_po_no(),
            "prNo": str(req.get("request_id", "")),
            "matCode": material_code,
            "qty": req.get("qty", 0),
            "unit": str(req.get("unit", "")),
            "unitPrice": int(round(float(req.get("unit_price") or 0) * 100)),
            "totalAmt": int(round(float(req.get("amount") or 0) * 100)),
            "supCode": supplier_code,
            "destWh": _norm(req.get("target_warehouse", "")),
            "orderStatus": seed_source.PO_STATUS_TO_ERP["created"],
            "gmtCreate": now_ms,
            "operatorId": str(req.get("operator_id", "")),
            # 幂等键透传留痕：我们这层不做去重，但要让上游能自己兜住重试
            "idempotencyKey": str(req.get("idempotency_key", "")),
        }

        get_fallback_store().put(_RUNTIME_NAMESPACE, record["poNo"], record)
        logger.info(f"[MOCK ERP] 已下达采购订单 {record['poNo']}（{material_code} × {record['qty']}）")
        return record

    def get_purchase_order(self, po_no: str) -> Optional[Dict[str, Any]]:
        chaos.apply("purchase")
        return find_purchase_order(po_no)

    def list_purchase_orders(self) -> List[Dict[str, Any]]:
        chaos.apply("purchase")

        merged: Dict[str, Dict[str, Any]] = {
            po["poNo"]: po for po in seed_source.purchase_orders_raw()
        }
        for item in get_fallback_store().search(_RUNTIME_NAMESPACE, limit=_SEARCH_ALL):
            if isinstance(item.value, dict) and item.value.get("poNo"):
                merged[item.value["poNo"]] = dict(item.value)

        # 按单号排序：装载链路每轮都要这份清单，顺序必须确定
        # （direct/mcp 两条路径的比对就靠它），字典序恰好与时间序一致。
        return sorted(merged.values(), key=lambda p: p["poNo"])
