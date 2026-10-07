"""
跨系统字段归一。

为什么不把「统一结构」交给各适配器自己保证
------------------------------------------
如果每个适配器各写一份映射，同一份规则就被抄了 N 遍：订单中台改一个字段名，
要在所有适配器、所有调用点同步改，漏一处就是线上读空字段。所以映射规则集中
在这里，适配器只负责「把上游返回原样交出来」，归一是**显式的一步**，
出错能定位到是哪一家的哪条规则。

真实上游的三种形状（也就是 mock 适配器刻意模仿的形状）
------------------------------------------------------
    订单中台  {"orderNo": "SO...", "tradeStatus": 40, "payFee": 19900,
               "gmtCreate": 1773..., "receiverInfo": {...}, "itemList": [...]}
    快递查询  {"waybillNo": "SF...", "expressCompany": "顺丰速运",
               "state": "派送中", "data": [{"ftime": "...", "context": "..."}]}
    仓储 WMS  {"skuCode": "SKU-...", "warehouseName": "华东仓",
               "qtyAvailable": 320, "qtyReserved": 12, "restockEta": ""}

对不上的地方一共四类，这也是归一层要处理的全部内容：

  1. **命名口径**：订单号 orderNo / 运单号 waybillNo / 商品编码 skuCode，
     同一个概念在三家有三个名字 —— 别名表解决。
  2. **枚举口径**：订单中台用 10/20/30/40/50，内部统一用 100/200/300/400/500；
     快递接口干脆只给中文状态 —— 状态表解决。
  3. **单位与量纲**：金额有的给「分」有的给「元」，时间有的是毫秒时间戳、
     有的是 `yyyy-MM-dd HH:mm:ss` 字符串 —— 按**来源字段名**判单位，不猜数值。
  4. **结构层级**：收货地址有的是嵌套 dict，有的是平铺字符串 —— 扁平化成一行。

上层节点只认标准字段，永远不需要知道这些差异。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.agent_logger import get_logger

logger = get_logger("integration_normalize")

# 东八区：业务系统的「今天」以国内时区为准
_TZ_CHINA = timezone(timedelta(hours=8))


# ==================== 一、命名口径：字段别名表 ====================

#: 标准字段 -> 上游可能出现的写法（按优先级排列，命中即止）
FIELD_MAP: Dict[str, List[str]] = {
    # ---- 订单主数据 ----
    "order_id":      ["order_id", "orderId", "orderNo", "order_no", "orderSn", "tradeNo", "tid", "订单号"],
    "customer_name": ["customer_name", "customerName", "buyerNick", "buyer_nick", "consignee", "客户姓名"],
    "status_code":   ["status_code", "statusCode", "tradeStatus", "trade_status", "orderStatus", "订单状态"],
    "pay_amount":    ["pay_amount", "payAmount", "payFee", "pay_fee", "payment", "actualAmount", "实付金额"],
    "total_amount":  ["total_amount", "totalAmount", "goodsAmount", "totalFee", "total_fee", "订单金额"],
    "shipping_fee":  ["shipping_fee", "shippingFee", "postFee", "post_fee", "freight", "运费"],
    "warehouse":     ["warehouse", "warehouseName", "warehouse_name", "stockName", "发货仓"],
    "created_at":    ["created_at", "createdAt", "gmtCreate", "createTime", "create_time", "orderTime", "下单时间"],
    "items":         ["items", "itemList", "item_list", "orderItems", "details", "商品明细"],
    "receiver":      ["receiver", "receiverInfo", "receiver_info", "address", "shippingAddress", "收货地址"],
    # ---- 订单行 ----
    "sku_id":        ["sku_id", "skuId", "skuCode", "sku_code", "itemCode", "outSkuId", "商品编码"],
    "product_name":  ["product_name", "productName", "itemName", "item_name", "goodsName", "商品名称"],
    "quantity":      ["quantity", "qty", "num", "count", "购买数量"],
    "unit_price":    ["unit_price", "unitPrice", "salePrice", "price", "单价"],
    # ---- 物流 ----
    "tracking_no":   ["tracking_no", "trackingNo", "waybillNo", "waybill_no", "mailNo", "nu", "运单号"],
    "carrier":       ["carrier", "expressCompany", "express_company", "company", "com", "承运商"],
    "trace_status":  ["trace_status", "traceStatus", "state", "logisticsStatus", "物流状态"],
    "nodes":         ["nodes", "data", "traces", "list", "轨迹"],
    "node_time":     ["node_time", "ftime", "acceptTime", "time", "datetime", "节点时间"],
    "node_desc":     ["node_desc", "context", "acceptStation", "desc", "remark", "节点描述"],
    "node_status":   ["node_status", "status", "state", "节点状态"],
    # ---- 库存 ----
    "available":     ["available", "qtyAvailable", "qty_available", "usableNum", "stock", "可售数量"],
    "reserved":      ["reserved", "qtyReserved", "qty_reserved", "lockNum", "占用数量"],
    "restock_eta":   ["restock_eta", "restockEta", "预计补货"],
}


# ==================== 二、枚举口径：状态码 / 状态文案 ====================

#: 内部统一状态码 -> 文案。全系统只认这一份。
STATUS_TEXT: Dict[str, str] = {
    "100": "待付款",
    "200": "已付款待发货",
    "300": "已发货",
    "400": "已签收",
    "500": "已取消",
    "600": "退款中",
    "700": "已退款",
}

#: 上游状态码 -> 内部状态码。
#: 订单中台（用友/金蝶这类 ERP 的常见约定）用 10/20/30/40/50，
#: 内部用百位口径。两边都要能进（幂等：已经归一过的不再二次映射，
#: 否则 400 会被当成「上游的 400」再映射一次 —— 虽然结果相同，
#: 但多一条假的归一轨迹会让人误以为上游真的返回了 400）。
STATUS_CODE_MAP: Dict[str, str] = {
    "10": "100", "20": "200", "30": "300", "40": "400", "50": "500",
    "60": "600", "70": "700",
    "100": "100", "200": "200", "300": "300", "400": "400",
    "500": "500", "600": "600", "700": "700",
}

#: 状态文案 -> 内部状态码（有些上游只给中文，没有码）
STATUS_TEXT_MAP: Dict[str, str] = {v: k for k, v in STATUS_TEXT.items()}

#: 快递接口的状态词表（与订单状态不是一套，单独维护）
TRACE_STATUS_HINTS = ["已揽收", "运输中", "派送中", "已签收", "异常", "退回", "待揽收"]


# ==================== 三、单位与量纲 ====================

#: 以「分」为单位的上游字段。
#:
#: 单位属于**来源字段**，不属于数值本身 —— 所以按字段名判定，不去猜数值大小。
#: 「199 元的整数」和「199 分」在数值上完全无法区分，靠大小猜迟早把
#: 199 元读成 1.99 元，或者把 19900 分读成 19900 元。
CENT_FIELDS = {
    "payFee", "pay_fee", "totalFee", "total_fee", "postFee", "post_fee",
    "freight", "actualAmount", "price", "salePrice", "unitPrice",
}


class MapTrace:
    """归一轨迹：记录「哪个上游字段被映射成了哪个标准字段」。

    存在的意义是可解释性。demo 里直接打印出来，能指着说清这三家的差异具体
    在哪几处；线上排查「字段读空了」时也不用回去翻上游文档。
    """

    def __init__(self, source: str = ""):
        self.source = source
        self.notes: List[str] = []

    def add(self, note: str) -> None:
        if note and note not in self.notes:
            self.notes.append(note)

    def render(self) -> str:
        prefix = f"[{self.source}] " if self.source else ""
        return prefix + "；".join(self.notes) if self.notes else prefix + "字段口径一致，无需映射"

    def lines(self) -> List[str]:
        prefix = f"[{self.source}] " if self.source else ""
        return [prefix + n for n in self.notes]


# ==================== 取值 ====================

def pick(raw: Dict[str, Any], canonical: str, *, trace: Optional[MapTrace] = None) -> Any:
    """按别名表从上游报文里取一个字段。

    空字符串与 None 一视同仁地跳过：上游把「没填」表示成 `""` 还是 `null`
    是不确定的，两种都得继续往下找别名。
    """
    if not isinstance(raw, dict):
        return None
    for alias in FIELD_MAP.get(canonical, [canonical]):
        if alias in raw and raw[alias] not in (None, ""):
            if trace is not None and alias != canonical:
                trace.add(f"{alias} → {canonical}")
            return raw[alias]
    return None


# ==================== 金额 ====================

def to_yuan(value: Any, *, source_field: str = "", trace: Optional[MapTrace] = None,
            canonical: str = "") -> float:
    """金额归一：分 -> 元。按**来源字段名**判单位，不猜数值。

    归一轨迹由这里一并写出（而不是先由 `pick` 记一条「改了名」、这里再记一条
    「换了单位」）：一个字段在报告里出现两行，读起来像是两处差异。
    """
    if value in (None, ""):
        return 0.0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        logger.warning(f"金额字段无法解析：{value!r}（来源 {source_field}）")
        return 0.0

    label = canonical or "金额"

    if source_field in CENT_FIELDS:
        yuan = round(amount / 100.0, 2)
        if trace is not None:
            trace.add(f"{source_field}(分) → {label} = {amount:.0f} 分即 {yuan:.2f} 元")
        return yuan

    if trace is not None and source_field and source_field != canonical:
        trace.add(f"{source_field} → {label}")
    return round(amount, 2)


# ==================== 时间 ====================

#: 上游可能给的时间字符串格式，按可能性排序
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y%m%d%H%M%S",
)

#: 超过这个数量级就按毫秒解释。秒级时间戳要到 2286 年才会越过 1e11，
#: 而毫秒级在 2001 年就已经是 1e12 —— 在业务时间范围内没有歧义，
#: 所以不需要上游额外提供单位字段。
_MS_THRESHOLD = 1e11


def format_time(value: Any, *, source_field: str = "", trace: Optional[MapTrace] = None,
                canonical: str = "") -> str:
    """时间归一：秒/毫秒时间戳、ISO 串、常见日期串 -> `yyyy-MM-dd HH:mm:ss`。"""
    if value in (None, ""):
        return ""

    label = canonical or "时间"
    moment: Optional[datetime] = None

    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        seconds = float(value)
        unit = "毫秒" if seconds > _MS_THRESHOLD else "秒"
        if unit == "毫秒":
            seconds /= 1000.0
        try:
            moment = datetime.fromtimestamp(seconds, tz=_TZ_CHINA)
        except (OverflowError, OSError, ValueError):
            logger.warning(f"时间戳超出可解析范围：{value!r}")
            return ""
        if trace is not None:
            trace.add(f"{source_field}({unit}时间戳) → {label} = {moment.strftime('%Y-%m-%d %H:%M:%S')}")

    elif isinstance(value, str):
        text = value.strip().replace("Z", "").split("+")[0]
        for fmt in _TIME_FORMATS:
            try:
                moment = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if moment is None:
            logger.warning(f"时间字段无法解析：{value!r}")
            return ""
        if trace is not None and source_field and source_field != canonical:
            trace.add(f"{source_field} → {label}")

    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_TZ_CHINA)
    return moment.astimezone(_TZ_CHINA).strftime("%Y-%m-%d %H:%M:%S")


# ==================== 地址 ====================

_PHONE = re.compile(r"\b(1[3-9]\d)\d{4}(\d{4})\b")
_ADDRESS_PARTS = ("province", "city", "district", "detail")


def mask_phone(text: str) -> str:
    """手机号脱敏：138****6721。

    收货信息会进日志、进状态、进前端页面，明文手机号属于不该到处跑的数据。
    种子里本来就是脱敏的，这里兜住上游给明文的情况。
    """
    return _PHONE.sub(r"\1****\2", text or "")


def flatten_address(value: Any, *, source_field: str = "",
                    trace: Optional[MapTrace] = None) -> str:
    """收货信息归一：嵌套 dict -> 一行文本，并顺手脱敏手机号。

    上游有两种形态：结构化地址对象（省/市/区/详址分开）和拼好的一整串。
    客服侧只需要一行能读的字符串，所以这里做**单向**扁平化：
    结构化对象拍平成「姓名 电话，省市区+详址」，已经是字符串的原样保留。

    归一轨迹也在这里写：改名与改结构是同一处差异，分两条记会让人以为有区别。
    """
    if not value:
        return ""

    if isinstance(value, str):
        if trace is not None and source_field and source_field != "receiver":
            trace.add(f"{source_field} → receiver")
        return mask_phone(value.strip())

    if isinstance(value, dict):
        parts = [str(value.get(k) or "").strip() for k in _ADDRESS_PARTS]
        address = "".join(p for p in parts if p)
        name = str(value.get("name") or "").strip()
        phone = mask_phone(str(value.get("phone") or "").strip())

        pieces = [p for p in (name, phone) if p]
        if trace is not None and address:
            trace.add(f"{source_field or 'receiver'}(嵌套地址) → receiver(扁平字符串)")

        if pieces:
            return f"{' '.join(pieces)}，{address}" if address else " ".join(pieces)
        return address

    return str(value)


# ==================== 状态 ====================

def normalize_status(value: Any, *, trace: Optional[MapTrace] = None) -> Tuple[str, str]:
    """状态归一，返回 (内部状态码, 文案)。

    三种输入都要认：上游数字码（40）、内部数字码（400）、中文文案（已签收）。
    认不出来时返回 `("", 原文)` —— 保留原文比硬猜一个码有用，
    上层可以照原样展示，而不是显示一个错的「已取消」。
    """
    if value in (None, ""):
        return "", ""

    if isinstance(value, str) and not value.strip().isdigit():
        text = value.strip()
        code = STATUS_TEXT_MAP.get(text, "")
        return code, text

    raw = str(value).strip()
    code = STATUS_CODE_MAP.get(raw, "")
    if not code:
        return "", raw

    if trace is not None and code != raw:
        trace.add(f"status={raw} → {code}({STATUS_TEXT.get(code, '')})")
    return code, STATUS_TEXT.get(code, "")


# ==================== 各业务对象的归一 ====================

def normalize_order(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """订单主数据归一。"""
    trace = trace if trace is not None else MapTrace("订单中台")

    order_id = pick(raw, "order_id", trace=trace) or ""
    status_code, status_text = normalize_status(pick(raw, "status_code"), trace=trace)

    return {
        "order_id": str(order_id),
        "customer_name": str(pick(raw, "customer_name", trace=trace) or ""),
        "status_code": status_code,
        "status_text": status_text,
        "pay_amount": _money(raw, "pay_amount", trace),
        "total_amount": _money(raw, "total_amount", trace),
        "shipping_fee": _money(raw, "shipping_fee", trace),
        "warehouse": str(pick(raw, "warehouse", trace=trace) or ""),
        "created_at": _time(raw, "created_at", trace),
        "receiver": flatten_address(pick(raw, "receiver"),
                                    source_field=_matched_alias(raw, "receiver"), trace=trace),
        "items": [
            normalize_order_item(item, trace=trace)
            for item in (pick(raw, "items", trace=trace) or [])
            if isinstance(item, dict)
        ],
    }


def normalize_order_item(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """订单行归一。"""
    price_field = _matched_alias(raw, "unit_price")
    return {
        "sku_id": str(pick(raw, "sku_id", trace=trace) or ""),
        "product_name": str(pick(raw, "product_name", trace=trace) or ""),
        "quantity": int(pick(raw, "quantity", trace=trace) or 0),
        "unit_price": to_yuan(
            pick(raw, "unit_price"), source_field=price_field,
            trace=trace, canonical="行单价",
        ),
    }


def normalize_logistics(raw: Dict[str, Any], *, order_id: str = "",
                        trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """物流轨迹归一。节点按时间**正序**排好，下游不用各自再排一遍。"""
    trace = trace if trace is not None else MapTrace("快递查询")

    nodes = []
    for node in (pick(raw, "nodes", trace=trace) or []):
        if not isinstance(node, dict):
            continue
        nodes.append({
            "time": _time(node, "node_time", trace, canonical_name="轨迹节点时间"),
            "status": str(pick(node, "node_status", trace=trace) or ""),
            "desc": str(pick(node, "node_desc", trace=trace) or ""),
        })
    nodes.sort(key=lambda n: n["time"] or "")

    return {
        "order_id": order_id or str(pick(raw, "order_id", trace=trace) or ""),
        "carrier": str(pick(raw, "carrier", trace=trace) or ""),
        "tracking_no": str(pick(raw, "tracking_no", trace=trace) or ""),
        "status": str(pick(raw, "trace_status", trace=trace) or ""),
        "nodes": nodes,
    }


def normalize_stock(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """库存归一。"""
    trace = trace if trace is not None else MapTrace("仓储 WMS")
    return {
        "sku_id": str(pick(raw, "sku_id", trace=trace) or ""),
        "warehouse": str(pick(raw, "warehouse", trace=trace) or ""),
        "available": int(pick(raw, "available", trace=trace) or 0),
        "reserved": int(pick(raw, "reserved", trace=trace) or 0),
        "restock_eta": str(pick(raw, "restock_eta", trace=trace) or ""),
    }


# ==================== 内部小工具 ====================

def _matched_alias(raw: Dict[str, Any], canonical: str) -> str:
    """返回该字段实际命中的来源字段名（用于判金额单位、写归一轨迹）。"""
    for alias in FIELD_MAP.get(canonical, [canonical]):
        if alias in raw and raw[alias] not in (None, ""):
            return alias
    return ""


def _money(raw: Dict[str, Any], canonical: str, trace: MapTrace) -> float:
    # 轨迹交给 to_yuan 一并写出，这里取值时不再记一条 —— 否则同一处差异会出现两行
    return to_yuan(pick(raw, canonical), source_field=_matched_alias(raw, canonical),
                   trace=trace, canonical=canonical)


def _time(raw: Dict[str, Any], canonical: str, trace: MapTrace, canonical_name: str = "") -> str:
    return format_time(pick(raw, canonical), source_field=_matched_alias(raw, canonical),
                       trace=trace, canonical=canonical_name or canonical)
