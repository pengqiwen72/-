"""
收货质检 mock（WMS），含写路径：登记收货并归档质检报告。

行为要点
--------
* 应收数量 / 物料 / 供应商**从采购单反查**，不要求调用方复述 —— 与真实 WMS
  一致（收货是对着一张单子做的），也避免调用方传了与订单不一致的数量把账做乱。
* 引用的采购单不存在 → 抛 `PurchaseOrderNotFound`（信封的 not_found 码），
  「这张单没法收」必须让上层看见，而不是静默落一条挂空的收货记录。
* 质检报告归档走 `FilePort`（mock 落本地 `data/runtime/objects/`，返回伪 OSS
  URL）：报告 URL 随回执一起回传，收货单里留 `reportUrl`。FilePort 不在 MCP
  网关里（见 `integrations/ports.py::FilePort`），网关服务端内部直调即可。
* 与采购 mock 同一条惯例：**端口不做去重**，重复提交由幂等守卫拦。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from integrations.mock import chaos, seed_source
from integrations.mock.purchase_mock import find_purchase_order
from integrations.ports import PurchaseOrderNotFound, ReceiptPort
from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("receipt_mock")

_TZ_CHINA = timezone(timedelta(hours=8))

#: 运行期登记的收货单（上游报文形状）。种子的收货单在 `("receipt",)`，
#: 字段口径不同，分开放 —— 理由同 `purchase_mock` 的说明。
_RUNTIME_NAMESPACE = ("rcv_runtime",)


def _new_rcv_no() -> str:
    return f"RC{datetime.now(_TZ_CHINA):%Y%m%d}{uuid.uuid4().hex[:5].upper()}"


def _render_report(record: Dict[str, Any]) -> str:
    """质检报告正文：数字全部来自这张收货单本身，人读得懂、可归档。"""
    actual = int(record.get("qtyActual") or 0)
    bad = int(record.get("qtyBad") or 0)
    rate = f"{bad / actual:.2%}" if actual > 0 else "—"
    stamp = datetime.fromtimestamp(
        int(record.get("gmtCreate") or 0) / 1000.0, tz=_TZ_CHINA
    ).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "收货质检报告",
        "=" * 32,
        f"收货单号：{record.get('rcvNo', '')}",
        f"采购订单：{record.get('poNo', '')}",
        f"物料：{record.get('matCode', '')}",
        f"收货仓库：{record.get('whCode', '')}    供应商：{record.get('supCode', '')}",
        f"应收：{record.get('qtyPlan', 0)}    实收：{actual}    不良：{bad}（{rate}）",
    ]
    if record.get("badType"):
        lines.append(f"不良类型：{record['badType']}")
    if record.get("disposal"):
        lines.append(f"处置方式：{record['disposal']}")
    lines += [
        f"登记人：{record.get('checker', '')}",
        f"登记时间：{stamp}",
        "（本报告由 WMS 收货登记流程自动生成，demo 数据均为虚构）",
    ]
    return "\n".join(lines) + "\n"


class MockReceiptPort(ReceiptPort):
    """读种子 + 运行期单；写收货登记记录。"""

    def submit_receipt(self, request: Dict[str, Any]) -> Dict[str, Any]:
        chaos.apply("receipt")

        req = dict(request or {})
        po_no = str(req.get("po_no") or "").strip().upper()

        po = find_purchase_order(po_no)
        if po is None:
            raise PurchaseOrderNotFound(f"采购订单 {po_no or '(空)'} 不存在，无法登记收货")

        now_ms = int(datetime.now(_TZ_CHINA).timestamp() * 1000)
        record: Dict[str, Any] = {
            "rcvNo": _new_rcv_no(),
            "poNo": po_no,
            "matCode": po["matCode"],
            "qtyPlan": po["qty"],
            "qtyActual": int(req.get("qty_received") or 0),
            "qtyBad": int(req.get("qty_defect") or 0),
            "badType": str(req.get("defect_type", "")),
            "disposal": str(req.get("disposition", "")),
            "whCode": str(req.get("warehouse_code") or po["destWh"]).strip().upper(),
            "supCode": po["supCode"],
            "checker": str(req.get("inspector", "")),
            "gmtCreate": now_ms,
            "idempotencyKey": str(req.get("idempotency_key", "")),
        }

        # 质检报告归档（FilePort 只是字节落盘，没有业务判断 —— 挂了不影响
        # 收货单本身成立，所以这里降级为「不带报告 URL」而不是整体失败）
        try:
            from integrations.factory import get_file_port

            obj = get_file_port().put_object(
                f"receipts/{record['rcvNo']}.txt", _render_report(record)
            )
            record["reportUrl"] = obj.get("url", "")
        except Exception as e:  # noqa: BLE001 —— 附件归档失败不该拦住收货登记
            logger.warning(f"[MOCK WMS] 质检报告归档失败（收货单照常登记）：{e}")

        get_fallback_store().put(_RUNTIME_NAMESPACE, record["rcvNo"], record)
        logger.info(f"[MOCK WMS] 已登记收货 {record['rcvNo']}（实收 {record['qtyActual']}，不良 {record['qtyBad']}）")
        return record

    def get_receipt(self, receipt_no: str) -> Optional[Dict[str, Any]]:
        chaos.apply("receipt")

        key = (receipt_no or "").strip().upper()
        if not key:
            return None

        item = get_fallback_store().get(_RUNTIME_NAMESPACE, key)
        if item is not None and isinstance(item.value, dict):
            return dict(item.value)

        for r in seed_source.receipts_raw():
            if r["rcvNo"] == key:
                return r
        return None
