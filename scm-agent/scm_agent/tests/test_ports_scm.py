"""
守的是端口层与韧性接线的七条线（`src/integrations/` + mock/*）。

  1. **端口表完整**：八行动作齐全，每行的 http / mock / mcp 类都真实存在；
     构造出的端口满足协议面的方法清单（端口抽象不是纸面上的）。
  2. **上游报文形状**：mock 吐的是 `matCode` / `refPrice`(分) / `orderStatus`
     两位码 —— 模仿的是 ERP/SRM/WMS，不是我们自己的标准结构。
  3. **归一映射**：分 → 元（按来源字段名判定）、两位码 → 内部码（幂等）、
     "Y"/"N" → bool、毫秒 → 可读时间 —— 这些是跑出来的，不是写死的。
  4. **写路径**：采购 / 收货 / 工单真的落库；引用不存在的实体抛 *NotFound；
     运行期记录落**独立命名空间**（上游报文形状），不混进种子。
  5. **对象存储**：质检报告落本地目录 + 伪 OSS URL，路径穿越被挡。
  6. **熔断可见**：`MOCK_FAIL_ACTIONS=material` 时前三次真失败、第四次快速
     失败，且只坏这一路 —— `load_degraded` 场景的单元版。
  7. **direct / mcp 逐字段一致**：同一批读走两条路径结果相等（种子相对时间
     戳除外，见比对处的容差说明）；写路径的 not_found 异常类也一致
     （靠信封的 `exception` 字段还原）。

连接池复用要起本地 socket 计数，标了 `slow`（`-m "not slow"` 可跳过）。
"""

from __future__ import annotations

import importlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import pytest

from integrations import factory
from integrations import ports as port_protocols
from integrations.factory import (
    get_file_port,
    get_handoff_port,
    get_inventory_port,
    get_material_port,
    get_purchase_port,
    get_receipt_port,
    get_supplier_port,
    get_warehouse_port,
    reset_ports,
)
from integrations.normalize import (
    MapTrace,
    format_time,
    normalize_material,
    normalize_status,
    normalize_supplier,
    to_yuan,
)
from integrations.ports import (
    IntegrationError,
    MaterialNotFound,
    PurchaseOrderNotFound,
    SupplierNotFound,
)
from integrations.resilience import BreakerState, CircuitOpen, get_breaker
from store.store_provider import get_fallback_store
from tools.store_tool import get_purchase_orders

#: 动作 -> (协议, 取端口函数)。表本身在用例里对照 `factory._PORT_TABLE`。
_PROTOCOLS = {
    "material": port_protocols.MaterialPort,
    "supplier": port_protocols.SupplierPort,
    "warehouse": port_protocols.WarehousePort,
    "inventory": port_protocols.InventoryPort,
    "purchase": port_protocols.PurchasePort,
    "receipt": port_protocols.ReceiptPort,
    "file": port_protocols.FilePort,
    "handoff": port_protocols.HandoffPort,
}

_GETTERS = {
    "material": get_material_port,
    "supplier": get_supplier_port,
    "warehouse": get_warehouse_port,
    "inventory": get_inventory_port,
    "purchase": get_purchase_port,
    "receipt": get_receipt_port,
    "file": get_file_port,
    "handoff": get_handoff_port,
}


# ---------------------------------------------------------------- 端口表

def test_port_table_rows_resolve_to_real_classes():
    assert set(factory._PORT_TABLE) == set(_PROTOCOLS)

    for action, (label, http_cls, mock_module, mock_cls, mcp_cls) in factory._PORT_TABLE.items():
        assert label, f"{action} 缺中文标签（日志里用得着）"
        assert hasattr(importlib.import_module("integrations.http.business_http"), http_cls)
        assert hasattr(importlib.import_module(f"integrations.mock.{mock_module}"), mock_cls)
        if action == "file":
            assert mcp_cls is None, "对象存储刻意不进网关（见 ports.py::FilePort）"
        else:
            assert mcp_cls and hasattr(
                importlib.import_module("integrations.mcp.ports"), mcp_cls
            ), f"{action} 的 mcp 类不存在"


def test_mock_ports_and_factory_portals_expose_the_protocol_surface():
    for action, protocol in _PROTOCOLS.items():
        # 实现侧：mock 类真的实现了协议（isinstance 在具体类上，不依赖代理）
        _, _, mock_module, mock_cls, _ = factory._PORT_TABLE[action]
        impl = getattr(importlib.import_module(f"integrations.mock.{mock_module}"), mock_cls)()
        assert isinstance(impl, protocol), f"{mock_cls} 没有实现 {protocol.__name__}"

        # 工厂侧：带熔断代理的端口对外仍是同一组方法
        port = _GETTERS[action]()
        methods = [
            name for name in dir(protocol)
            if not name.startswith("_") and callable(getattr(protocol, name))
        ]
        assert methods, f"{protocol.__name__} 一个方法都没有？"
        for name in methods:
            assert callable(getattr(port, name)), f"{action} 端口缺方法 {name}"


# ---------------------------------------------------------------- 归一映射

def test_normalize_material_maps_erp_wire_shape():
    trace = MapTrace()
    out = normalize_material({
        "matCode": "M100023", "matName": "冷轧钢板 1.2mm", "specModel": "1.2mm×1250mm×C",
        "matGroup": "金属原材料", "baseUnit": "kg", "refPrice": 560,
        "substituteCodes": ["M100031"],
    }, trace=trace)

    assert out == {
        "material_code": "M100023", "material_name": "冷轧钢板 1.2mm",
        "spec": "1.2mm×1250mm×C", "category": "金属原材料", "unit": "kg",
        "unit_price_ref": 5.6, "substitutes": ["M100031"],
    }
    assert "matCode → material_code" in trace.notes
    assert "refPrice(分) → 参考单价 = 560 分即 5.60 元" in trace.notes


def test_normalize_status_is_idempotent_across_three_wire_forms():
    trace = MapTrace()
    assert normalize_status("30", trace=trace) == ("received", "已入库")
    assert "状态码 30 → received（已入库）" in trace.notes
    assert normalize_status("received") == ("received", "已入库")   # 内部机器码：幂等
    assert normalize_status("已入库") == ("received", "已入库")     # 只给中文文案的上游
    assert normalize_status("99") == ("", "99"), "认不出来就保留原文，不许硬猜"
    assert normalize_status(None) == ("", "")


def test_normalize_supplier_accepts_y_n_booleans():
    assert normalize_supplier({"supCode": "S001", "isNewSup": "Y"})["is_new"] is True
    assert normalize_supplier({"supCode": "S002", "isNewSup": "N"})["is_new"] is False
    assert normalize_supplier({"supCode": "S003", "isNewSup": True})["is_new"] is True


def test_cent_policy_reads_only_registered_source_fields_as_cents():
    """单位按来源字段名判定：名单里的按分读，不认识的按元读（宁可读大）。"""
    assert to_yuan(560, source_field="refPrice") == 5.6
    assert to_yuan(560, source_field="quotPrice") == 5.6
    assert to_yuan(560, source_field="amount") == 560.0, "不静默缩小 100 倍"


def test_format_time_accepts_epoch_ms_and_text():
    stamp = format_time(1773000000000)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stamp)
    assert stamp.startswith("2026-")
    assert format_time("2026-03-01 10:00:00") == "2026-03-01 10:00:00"
    assert format_time(None) == ""
    assert format_time("不是时间") == "", "解析不了留空，不抛"


# ---------------------------------------------------------------- mock 报文形状与装载链路

def test_mock_serves_upstream_shapes_and_load_path_normalizes_them():
    raw_material = next(
        m for m in get_material_port().list_materials() if m["matCode"] == "M100023"
    )
    assert raw_material["refPrice"] == 560 and raw_material["baseUnit"] == "kg"
    assert "material_code" not in raw_material, "mock 吐的是上游形状，不是内部口径"

    raw_order = next(
        o for o in get_purchase_port().list_purchase_orders() if o["poNo"] == "PO20260928001"
    )
    assert raw_order["orderStatus"] == "20"  # 两位码，不是 in_transit

    # store_tool 是「取数 → 归一 → 模型」的收口：出来的必须是内部口径
    from tools.store_tool import get_materials

    material = next(m for m in get_materials() if m.material_code == "M100023")
    assert material.material_name == "冷轧钢板 1.2mm"
    assert material.unit_price_ref == 5.6
    assert material.substitutes == ["M100031"]

    order = next(o for o in get_purchase_orders() if o.po_no == "PO20260928001")
    assert order.status == "in_transit" and order.status_text == "在途"
    assert order.amount == 44800.0 and order.supplier_code == "S012" and order.qty == 8000


# ---------------------------------------------------------------- 写路径

def _po_request(**overrides) -> Dict[str, Any]:
    request = {
        "request_id": "PR20261009000001",
        "material_code": "M100023",
        "qty": 3000,
        "unit": "kg",
        "unit_price": 5.6,
        "amount": 16800.0,
        "supplier_code": "S007",
        "target_warehouse": "WH01",
        "operator_id": "OP001",
        "idempotency_key": "unit-key-1",
    }
    request.update(overrides)
    return request


def test_create_purchase_order_roundtrip_and_runtime_namespace():
    port = get_purchase_port()
    po = port.create_purchase_order(_po_request())

    # 元 → 分由写路径换算（内部请求是元，上游 ERP 收分）
    assert po["unitPrice"] == 560 and po["totalAmt"] == 1680000
    assert po["orderStatus"] == "10"  # created 的 ERP 两位码

    assert port.get_purchase_order(po["poNo"]) == po, "回查要拿到同一张单"
    assert any(p["poNo"] == po["poNo"] for p in port.list_purchase_orders())

    # 运行期单落独立命名空间（上游报文形状），不混进种子所在的命名空间
    item = get_fallback_store().get(("po_runtime",), po["poNo"])
    assert item is not None
    assert item.value["poNo"] == po["poNo"] and "matCode" in item.value


def test_create_purchase_order_rejects_unknown_entities():
    port = get_purchase_port()
    # 引用不存在的物料 / 供应商 —— 「这次写没法完成」必须抛出来，不许静默落单
    with pytest.raises(MaterialNotFound):
        port.create_purchase_order(_po_request(material_code="M999999"))
    with pytest.raises(SupplierNotFound):
        port.create_purchase_order(_po_request(supplier_code="S999"))


def test_submit_receipt_looks_up_the_po_and_archives_the_report(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_OBJECT_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_OBJECT_BASE", "oss://demo-bucket")
    reset_ports()

    port = get_receipt_port()
    with pytest.raises(PurchaseOrderNotFound):
        port.submit_receipt({"po_no": "PO99999999", "qty_received": 1,
                             "idempotency_key": "rcv-bad-1"})

    record = port.submit_receipt({
        "po_no": "PO20260928002", "qty_received": 980, "qty_defect": 20,
        "defect_type": "磕碰变形", "disposition": "让步接收",
        "warehouse_code": "WH02", "inspector": "OP003", "idempotency_key": "rcv-test-1",
    })

    # 应收量 / 物料 / 供应商由采购单反查，不要求调用方复述
    assert record["matCode"] == "M400301" and record["qtyPlan"] == 1000
    assert record["supCode"] == "S004"
    assert record["qtyActual"] == 980 and record["qtyBad"] == 20

    # 质检报告归档：伪 OSS URL + 磁盘上的正文（数字全部来自这张单）
    assert record["reportUrl"].startswith("oss://demo-bucket/")
    rel = record["reportUrl"].split("oss://demo-bucket/", 1)[1]
    report = (tmp_path / rel).read_text(encoding="utf-8")
    assert record["rcvNo"] in report
    assert "实收：980" in report and "不良：20（2.04%）" in report

    assert port.get_receipt(record["rcvNo"]) == record
    item = get_fallback_store().get(("rcv_runtime",), record["rcvNo"])
    assert item is not None and item.value["rcvNo"] == record["rcvNo"]


def test_file_port_blocks_path_traversal_and_keeps_url_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_OBJECT_DIR", str(tmp_path))
    reset_ports()

    obj = get_file_port().put_object("../escape/evil.txt", "x")
    assert ".." not in obj["objectKey"]
    assert (tmp_path / obj["objectKey"]).exists(), "去掉 `..` 后要落在根目录里面"
    assert obj["url"].startswith("oss://scm-demo/")

    with pytest.raises(ValueError):
        get_file_port().put_object("", "x")


def test_handoff_ticket_lands_in_its_own_runtime_namespace():
    ticket = get_handoff_port().create_ticket({
        "reason": "置信度不足", "summary": "问答置信度低于阈值",
        "priority": "high", "thread_id": "thread-unit-1", "operator_id": "OP001",
        "idempotency_key": "ticket-unit-1",
    })
    assert ticket["ticketStatus"] == "open" and ticket["ticketNo"].startswith("TK")
    item = get_fallback_store().get(("ticket_runtime",), ticket["ticketNo"])
    assert item is not None and item.value["reason"] == "置信度不足"


# ---------------------------------------------------------------- 熔断可见（load_degraded 的单元版）

def test_failure_injection_trips_breaker_and_fast_fails_on_the_fourth_call(monkeypatch):
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "material")
    reset_ports()

    port = get_material_port()
    for _ in range(3):
        with pytest.raises(IntegrationError) as err:
            port.list_materials()
        assert not isinstance(err.value, CircuitOpen), "前三次是真失败，还不是快速失败"

    assert get_breaker("material").state is BreakerState.OPEN
    with pytest.raises(CircuitOpen):
        port.list_materials()

    # 坏的只是物料这一路 —— 「部分失败不整体失败」在端口层同样成立
    assert get_supplier_port().list_suppliers()

    # 撤掉注入并重置后恢复（场景切换靠的就是 reset_ports）
    monkeypatch.delenv("MOCK_FAIL_ACTIONS")
    reset_ports()
    assert get_material_port().list_materials()


def test_load_path_failure_is_isolated_per_domain(monkeypatch):
    """装载链路（store_tool）：物料域抛错，其它数据域照常 —— 节点层按域 try/except 的前提。"""
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "material")
    reset_ports()

    from tools import store_tool

    with pytest.raises(IntegrationError):
        store_tool.get_materials()
    assert store_tool.get_suppliers()
    assert store_tool.get_stock_items()


# ---------------------------------------------------------------- direct / mcp 逐字段一致

def _read_snapshot() -> Dict[str, Any]:
    """可读端口各取一份，全部按上游报文形状（还没归一的原始货）。"""
    return {
        "materials": get_material_port().list_materials(),
        "substitutes": get_material_port().list_substitutes("M100023"),
        "material_one": get_material_port().get_material("M100023"),
        "material_missing": get_material_port().get_material("M999999"),
        "suppliers": get_supplier_port().list_suppliers(),
        "quotes": get_supplier_port().list_supplier_quotes("M100023"),
        "warehouses": get_warehouse_port().list_warehouses(),
        "stock": get_inventory_port().list_stock(),
        "stock_by_material": get_inventory_port().list_stock_by_material("M100023"),
        "stock_by_warehouse": get_inventory_port().list_stock_by_warehouse("WH01"),
        "purchase_orders": get_purchase_port().list_purchase_orders(),
        "po_one": get_purchase_port().get_purchase_order("PO20260928001"),
        "receipt_one": get_receipt_port().get_receipt("RC20261002001"),
        "receipt_missing": get_receipt_port().get_receipt("RC999999"),
    }


#: 种子时间是「now − N 天」在**每次读取的时刻**现算的（resolve_offset，防演示
#: 数据过期），两条路径的两次读取相隔几秒（含网关冷启动），派生出来的时间戳
#: 天然有几秒差 —— 这是种子设计，不是端口分歧。逐字段比对只对它开容差。
_VOLATILE_MS_FIELDS = {"gmtCreate"}
_VOLATILE_TOLERANCE_MS = 60_000


def _assert_reads_agree(direct: Any, via: Any, path: str = "$") -> None:
    if isinstance(direct, dict) and isinstance(via, dict):
        assert set(direct) == set(via), f"{path} 字段集合不同"
        for key in direct:
            _assert_reads_agree(direct[key], via[key], f"{path}.{key}")
    elif isinstance(direct, list) and isinstance(via, list):
        assert len(direct) == len(via), f"{path} 长度不同"
        for i, (a, b) in enumerate(zip(direct, via)):
            _assert_reads_agree(a, b, f"{path}[{i}]")
    elif path.rsplit(".", 1)[-1] in _VOLATILE_MS_FIELDS:
        assert abs(direct - via) <= _VOLATILE_TOLERANCE_MS, (
            f"{path} 时间漂移超过容差：{direct} vs {via}"
        )
    else:
        assert direct == via, f"{path} 不一致：{direct!r} vs {via!r}"


def test_direct_and_mcp_reads_agree_field_by_field(monkeypatch):
    pytest.importorskip("mcp")

    monkeypatch.setenv("TOOL_GATEWAY", "direct")
    reset_ports()
    direct = _read_snapshot()
    assert direct["materials"] and direct["stock"], "底座没数据的话，这条比对等于空转"

    monkeypatch.setenv("TOOL_GATEWAY", "mcp")
    reset_ports()
    via_gateway = _read_snapshot()

    _assert_reads_agree(direct, via_gateway)


def test_direct_and_mcp_writes_agree_including_not_found_classes(monkeypatch):
    """写路径的一致性有两半：成功回执逐字段相同，失败时**异常类**也相同。"""
    pytest.importorskip("mcp")

    ticket_request = {
        "reason": "置信度不足", "summary": "演示两条路径一致",
        "priority": "high", "thread_id": "thread-parity-1", "operator_id": "OP001",
        "idempotency_key": "parity-ticket-1",
    }

    monkeypatch.setenv("TOOL_GATEWAY", "direct")
    reset_ports()
    direct_po = get_purchase_port().create_purchase_order(_po_request())
    direct_ticket = get_handoff_port().create_ticket(dict(ticket_request))
    with pytest.raises(MaterialNotFound):
        get_purchase_port().create_purchase_order(_po_request(material_code="M999999"))
    with pytest.raises(PurchaseOrderNotFound):
        get_receipt_port().submit_receipt(
            {"po_no": "PO99999999", "qty_received": 1, "idempotency_key": "parity-bad-1"}
        )

    monkeypatch.setenv("TOOL_GATEWAY", "mcp")
    reset_ports()
    via_po = get_purchase_port().create_purchase_order(_po_request())
    via_ticket = get_handoff_port().create_ticket(dict(ticket_request))
    # 信封里带了原始异常类名，not_found 还原成与直连相同的具体类
    with pytest.raises(MaterialNotFound):
        get_purchase_port().create_purchase_order(_po_request(material_code="M999999"))
    with pytest.raises(PurchaseOrderNotFound):
        get_receipt_port().submit_receipt(
            {"po_no": "PO99999999", "qty_received": 1, "idempotency_key": "parity-bad-2"}
        )

    # 单号与时间戳由上游生成（两边各生成各的），其余字段逐一对齐
    for field in ("prNo", "matCode", "qty", "unit", "unitPrice", "totalAmt",
                  "supCode", "destWh", "orderStatus", "operatorId", "idempotencyKey"):
        assert via_po[field] == direct_po[field], f"下单回执 {field} 不一致"
    for field in ("ticketStatus", "reason", "summary", "priority", "threadId", "operatorId"):
        assert via_ticket[field] == direct_ticket[field], f"工单回执 {field} 不一致"


def test_file_port_stays_local_even_in_mcp_mode(monkeypatch, tmp_path):
    """对象存储不进网关：mcp 模式下仍然直接落本地（表里 mcp_cls=None 的行为面）。"""
    pytest.importorskip("mcp")

    monkeypatch.setenv("TOOL_GATEWAY", "mcp")
    monkeypatch.setenv("MOCK_OBJECT_DIR", str(tmp_path))
    reset_ports()

    obj = get_file_port().put_object("receipts/RCX.txt", "hello")
    assert (tmp_path / obj["objectKey"]).read_text(encoding="utf-8") == "hello"


# ---------------------------------------------------------------- 连接池（slow）

@pytest.mark.slow
def test_http_transport_reuses_pooled_connections(monkeypatch):
    """5 次请求复用同一条 TCP 连接 —— 本地计数端点验证连接池真的在工作。"""
    from integrations.http import business_http

    hits: List[str] = []
    connections: List[int] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # 不声明则响应完就关，keep-alive 无从谈起

        def do_GET(self):  # noqa: N802 —— 标准库的回调命名
            hits.append(self.path)
            body = b'{"ok": true, "data": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def setup(self):
            super().setup()
            connections.append(1)  # 每个 TCP 连接进 setup 一次

        def log_message(self, *args):  # 静音访问日志
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("INTEGRATION_MODE", "http")
        monkeypatch.setenv("ERP_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
        reset_ports()
        business_http.reset_http_session()

        port = get_material_port()
        for _ in range(5):
            assert port.list_materials() == []
    finally:
        server.shutdown()
        server.server_close()
        business_http.reset_http_session()

    assert hits == ["/api/material/list"] * 5
    assert len(connections) == 1, f"连接没有被复用：{len(connections)} 条 TCP 连接"
