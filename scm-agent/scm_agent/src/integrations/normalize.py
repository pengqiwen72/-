"""
跨系统字段归一。

为什么不把「统一结构」交给各适配器自己保证
------------------------------------------
如果每个适配器各写一份映射，同一份规则就被抄了 N 遍：ERP 物料模块改一个
字段名，要在所有适配器、所有调用点同步改，漏一处就是线上读空字段。所以
映射规则集中在这里，适配器只负责「把上游返回原样交出来」，归一是**显式的
一步**（装载层、收货链路都在调），出错能定位到是哪一家的哪条规则。

四种上游系统的形状（也就是 mock 适配器刻意模仿的形状）
------------------------------------------------------
    ERP 物料   {"matCode": "M100023", "matName": "冷轧钢板 1.2mm",
                "specModel": "...", "matGroup": "...", "baseUnit": "kg",
                "refPrice": 560, "substituteCodes": ["M100031"]}
    SRM 供应商 {"supCode": "S004", "isNewSup": false, "leadDays": 5,
                "onTimeRate": 0.96, "defectRate": 0.012, ...}
    SRM 报价   {"matCode": "...", "supCode": "...", "quotPrice": 550,
                "minOrderQty": 500, "validDays": 30}
    WMS 库存   {"matCode": "...", "whCode": "WH02", "onHandQty": 320,
                "frozenQty": 12, "safetyQty": 400, "avgOut30d": 26.5}
    ERP 采购   {"poNo": "...", "orderStatus": "30", "unitPrice": 560,
                "totalAmt": 448000, "gmtCreate": 1773...}
    WMS 收货   {"rcvNo": "RC...", "qtyPlan": 8000, "qtyActual": 7920,
                "qtyBad": 80, "badType": "锈蚀", "checker": "..."}

对不上的地方一共四类，这也是归一层要处理的全部内容：

  1. **命名口径**：物料编码有 matCode，供应商编码有 supCode，仓库编码有
     whCode —— 同一个概念在四家系统各有各的叫法 —— 别名表解决。
  2. **枚举口径**：ERP 采购单用两位码 10/20/30/40 表示状态，SRM 用
     `isNewSup: "Y"/"N"` 表示布尔 —— 状态表与 `_bool()` 解决。
  3. **单位与量纲**：金额有的给「分」（refPrice / quotPrice / unitPrice /
     totalAmt）有的给「元」，时间统一是 epoch **毫秒** —— 按**来源字段名**
     判单位，不猜数值。
  4. **同一概念两套字段**：参考单价（物料侧 refPrice）与报价单价（SRM 侧
     quotPrice）是两种业务含义、两个字段名，不是同一个字段的两种写法 ——
     所以别名表按「目标对象」分组，不搞全局大杂烩。

上层节点只认标准字段，永远不需要知道这些差异。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.agent_logger import get_logger

logger = get_logger("integration_normalize")

# 东八区：业务系统的「今天」以国内时区为准
_TZ_CHINA = timezone(timedelta(hours=8))


# ==================== 一、命名口径：字段别名表 ====================

#: 标准字段 -> 上游可能出现的写法（按优先级排列，命中即止）
FIELD_MAP: Dict[str, List[str]] = {
    # ---- 物料（ERP 物料模块） ----
    "material_code": ["material_code", "matCode", "materialNo", "itemCode", "物料编码"],
    "material_name": ["material_name", "matName", "materialName", "itemName", "物料名称"],
    "spec":         ["spec", "specModel", "specification", "规格型号"],
    "category":     ["category", "matGroup", "materialGroup", "物料分类"],
    "unit":         ["unit", "baseUnit", "uom", "unitName", "计量单位"],
    "unit_price_ref": ["unit_price_ref", "refPrice", "standardPrice", "参考单价"],
    "substitutes":  ["substitutes", "substituteCodes", "alternatives", "替代料"],
    # ---- 供应商（SRM） ----
    "supplier_code": ["supplier_code", "supCode", "supplierNo", "vendorCode", "供应商编码"],
    "supplier_name": ["supplier_name", "supName", "supplierName", "vendorName", "供应商名称"],
    "is_new":       ["is_new", "isNewSup", "isNew", "newSupplier", "新供应商"],
    "lead_time_days": ["lead_time_days", "leadDays", "leadTime", "承诺交期"],
    "on_time_rate": ["on_time_rate", "onTimeRate", "deliveryOnTimeRate", "准时率"],
    "defect_rate":  ["defect_rate", "defectRate", "qualityDefectRate", "不良率"],
    "cooperation_since": ["cooperation_since", "cooperationSince", "合作起始"],
    "main_categories": ["main_categories", "mainCategories", "主营品类"],
    # ---- 仓库（WMS 基础档案） ----
    "warehouse_code": ["warehouse_code", "whCode", "warehouseNo", "仓库编码"],
    "warehouse_name": ["warehouse_name", "whName", "warehouseName", "仓库名称"],
    "city":         ["city", "所在城市"],
    "address":      ["address", "addr", "addrDetail", "详细地址"],
    # ---- 库存（WMS） ----
    "on_hand":      ["on_hand", "onHandQty", "onHand", "stockQty", "在库数量"],
    "reserved":     ["reserved", "frozenQty", "frozen", "lockedQty", "冻结数量"],
    "safety_stock": ["safety_stock", "safetyQty", "safetyStock", "安全库存"],
    "avg_daily_out": ["avg_daily_out", "avgOut30d", "avgDailyOut", "日均出库"],
    # ---- 报价（SRM） ----
    "min_order_qty": ["min_order_qty", "minOrderQty", "moq", "最小起订量"],
    "valid_days":   ["valid_days", "validDays", "quoteValidDays", "报价有效天数"],
    # ---- 采购订单（ERP 采购模块） ----
    "po_no":        ["po_no", "poNo", "purchaseOrderNo", "采购订单号"],
    "request_id":   ["request_id", "prNo", "requestNo", "申请单号"],
    "qty":          ["qty", "quantity", "orderQty", "数量"],
    "unit_price":   ["unit_price", "unitPrice", "quotPrice", "quotePrice", "price", "单价"],
    "amount":       ["amount", "totalAmt", "totalAmount", "金额"],
    "target_warehouse": ["target_warehouse", "destWh", "destWarehouse", "到货仓库"],
    "status":       ["status", "orderStatus", "statusCode", "status_code", "单据状态"],
    "operator_id":  ["operator_id", "operatorId", "operator", "经办人"],
    "created_at":   ["created_at", "gmtCreate", "createTime", "创建时间"],
    # ---- 收货（WMS） ----
    "receipt_no":   ["receipt_no", "rcvNo", "receiptNo", "收货单号"],
    "qty_expected": ["qty_expected", "qtyPlan", "expectedQty", "应收数量"],
    "qty_received": ["qty_received", "qtyActual", "receivedQty", "实收数量"],
    "qty_defect":   ["qty_defect", "qtyBad", "defectQty", "不良数量"],
    "defect_type":  ["defect_type", "badType", "defectType", "不良类型"],
    "disposition":  ["disposition", "disposal", "处置方式"],
    "inspector":    ["inspector", "checker", "质检员"],
    "report_url":   ["report_url", "reportUrl", "报告地址"],
}


# ==================== 二、枚举口径：状态码 / 状态文案 / 布尔 ====================

#: 内部统一状态码 -> 文案。全系统只认这一份。
STATUS_TEXT: Dict[str, str] = {
    "created": "待发货",
    "in_transit": "在途",
    "received": "已入库",
    "cancelled": "已取消",
}

#: 上游状态码 -> 内部状态码。
#: ERP 采购模块用两位码（10/20/30/40），内部用英文机器码。两边都要能进
#: （幂等：已经归一过的不再二次映射 —— 虽然结果相同，但多一条假的归一
#: 轨迹会让人误以为上游真的返回了英文码）。
STATUS_CODE_MAP: Dict[str, str] = {
    "10": "created", "20": "in_transit", "30": "received", "40": "cancelled",
    "created": "created", "in_transit": "in_transit",
    "received": "received", "cancelled": "cancelled",
}

#: 状态文案 -> 内部状态码（有些上游只给中文，没有码）
STATUS_TEXT_MAP: Dict[str, str] = {v: k for k, v in STATUS_TEXT.items()}

#: 布尔词的字符串写法（SRM 的 isNewSup 给的是 "Y"/"N"）
_TRUE_WORDS = {"y", "yes", "true", "1", "是"}


def _bool(value: Any) -> bool:
    """上游的布尔：真布尔 / "Y"/"N" / 1/0 / "是"。认不出来一律按 False。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in _TRUE_WORDS


# ==================== 三、单位与量纲 ====================

#: 以「分」为单位的上游字段。
#:
#: 单位属于**来源字段**，不属于数值本身 —— 所以按字段名判定，不去猜数值大小。
#: 「5.6 元」和「5.6 分」在数值上完全无法区分，靠大小猜迟早把 560 分读成
#: 560 元，或者把 6 元读成 0.06 元。名单只登记**已知以分报价**的字段；
#: 不在名单里的一律按元读 —— 宁可读成「偏大的元」，也不静默缩小 100 倍。
CENT_FIELDS = {
    "refPrice", "quotPrice", "quotePrice", "unitPrice", "standardPrice",
    "totalAmt", "price",
}


class MapTrace:
    """归一轨迹：记录「哪个上游字段被映射成了哪个标准字段」。

    存在的意义是可解释性。demo 里直接打印出来，能指着说清这四家的差异具体
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


# ==================== 状态 ====================

def normalize_status(value: Any, *, trace: Optional[MapTrace] = None) -> Tuple[str, str]:
    """状态归一，返回 (内部状态码, 文案)。

    三种输入都要认：上游两位码（30）、内部机器码（received）、中文文案（已入库）。
    认不出来时返回 `("", 原文)` —— 保留原文比硬猜一个码有用，
    上层可以照原样展示，而不是显示一个错的「已取消」。
    """
    if value in (None, ""):
        return "", ""

    if isinstance(value, str) and not value.strip().isdigit():
        text = value.strip()
        if text in STATUS_TEXT:  # 已经是内部机器码（幂等：不再二次映射）
            return text, STATUS_TEXT[text]
        code = STATUS_TEXT_MAP.get(text, "")
        return code, text

    raw = str(value).strip()
    code = STATUS_CODE_MAP.get(raw, "")
    if not code:
        return "", raw

    if trace is not None and code != raw:
        trace.add(f"状态码 {raw} → {code}（{STATUS_TEXT.get(code, '')}）")
    return code, STATUS_TEXT.get(code, "")


# ==================== 各业务对象的归一 ====================

def normalize_material(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """物料主数据归一（ERP 物料模块 -> `MaterialInfo` 字段口径）。"""
    trace = trace if trace is not None else MapTrace("ERP 物料")
    return {
        "material_code": str(pick(raw, "material_code", trace=trace) or ""),
        "material_name": str(pick(raw, "material_name", trace=trace) or ""),
        "spec": str(pick(raw, "spec", trace=trace) or ""),
        "category": str(pick(raw, "category", trace=trace) or ""),
        "unit": str(pick(raw, "unit", trace=trace) or ""),
        "unit_price_ref": _money(raw, "unit_price_ref", trace, label="参考单价"),
        "substitutes": _str_list(raw, "substitutes", trace),
    }


def normalize_supplier(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """供应商归一（SRM -> `SupplierInfo` 字段口径）。"""
    trace = trace if trace is not None else MapTrace("SRM 供应商")
    return {
        "supplier_code": str(pick(raw, "supplier_code", trace=trace) or ""),
        "supplier_name": str(pick(raw, "supplier_name", trace=trace) or ""),
        "is_new": _bool(pick(raw, "is_new", trace=trace)),
        "lead_time_days": _int(raw, "lead_time_days", trace),
        "on_time_rate": _float(raw, "on_time_rate", trace),
        "defect_rate": _float(raw, "defect_rate", trace),
        "cooperation_since": str(pick(raw, "cooperation_since", trace=trace) or ""),
        "main_categories": _str_list(raw, "main_categories", trace),
    }


def normalize_warehouse(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """仓库归一（WMS 基础档案 -> `WarehouseInfo` 字段口径）。"""
    trace = trace if trace is not None else MapTrace("WMS 仓库")
    return {
        "warehouse_code": str(pick(raw, "warehouse_code", trace=trace) or ""),
        "warehouse_name": str(pick(raw, "warehouse_name", trace=trace) or ""),
        "city": str(pick(raw, "city", trace=trace) or ""),
        "address": str(pick(raw, "address", trace=trace) or ""),
    }


def normalize_stock(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """库存归一（WMS -> `StockItem` 字段口径）。

    上游给的是四个**原始量**；「可用量」（在库 − 冻结）不在这里算 ——
    那是 `StockItem.qty_available` 的业务口径，只有一处定义。
    """
    trace = trace if trace is not None else MapTrace("WMS 库存")
    return {
        "material_code": str(pick(raw, "material_code", trace=trace) or ""),
        "warehouse_code": str(pick(raw, "warehouse_code", trace=trace) or ""),
        "on_hand": _int(raw, "on_hand", trace),
        "reserved": _int(raw, "reserved", trace),
        "safety_stock": _int(raw, "safety_stock", trace),
        "avg_daily_out": _float(raw, "avg_daily_out", trace),
    }


def normalize_quote(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """报价归一（SRM -> `SupplierQuote` 字段口径）。"""
    trace = trace if trace is not None else MapTrace("SRM 报价")
    return {
        "material_code": str(pick(raw, "material_code", trace=trace) or ""),
        "supplier_code": str(pick(raw, "supplier_code", trace=trace) or ""),
        "unit_price": _money(raw, "unit_price", trace, label="报价单价"),
        "min_order_qty": _int(raw, "min_order_qty", trace),
        "valid_days": _int(raw, "valid_days", trace),
    }


def normalize_purchase_order(raw: Dict[str, Any], *,
                             trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """采购订单归一（ERP 采购模块 -> `PurchaseOrderInfo` 字段口径）。"""
    trace = trace if trace is not None else MapTrace("ERP 采购")
    status_code, status_text = normalize_status(pick(raw, "status", trace=trace), trace=trace)
    return {
        "po_no": str(pick(raw, "po_no", trace=trace) or ""),
        "request_id": str(pick(raw, "request_id", trace=trace) or ""),
        "material_code": str(pick(raw, "material_code", trace=trace) or ""),
        "qty": _float(raw, "qty", trace),
        "unit": str(pick(raw, "unit", trace=trace) or ""),
        "unit_price": _money(raw, "unit_price", trace, label="采购单价"),
        "amount": _money(raw, "amount", trace, label="订单金额"),
        "supplier_code": str(pick(raw, "supplier_code", trace=trace) or ""),
        "target_warehouse": str(pick(raw, "target_warehouse", trace=trace) or ""),
        "status": status_code,
        "status_text": status_text,
        "operator_id": str(pick(raw, "operator_id", trace=trace) or ""),
    }


def normalize_receipt(raw: Dict[str, Any], *, trace: Optional[MapTrace] = None) -> Dict[str, Any]:
    """收货单归一（WMS -> 内部字段口径；`reportUrl` 缺席时留空串）。"""
    trace = trace if trace is not None else MapTrace("WMS 收货")
    return {
        "receipt_no": str(pick(raw, "receipt_no", trace=trace) or ""),
        "po_no": str(pick(raw, "po_no", trace=trace) or ""),
        "material_code": str(pick(raw, "material_code", trace=trace) or ""),
        "qty_expected": _int(raw, "qty_expected", trace),
        "qty_received": _int(raw, "qty_received", trace),
        "qty_defect": _int(raw, "qty_defect", trace),
        "defect_type": str(pick(raw, "defect_type", trace=trace) or ""),
        "disposition": str(pick(raw, "disposition", trace=trace) or ""),
        "warehouse_code": str(pick(raw, "warehouse_code", trace=trace) or ""),
        "supplier_code": str(pick(raw, "supplier_code", trace=trace) or ""),
        "inspector": str(pick(raw, "inspector", trace=trace) or ""),
        "created_at": _time(raw, "created_at", trace, label="登记时间"),
        "report_url": str(pick(raw, "report_url", trace=trace) or ""),
    }


# ==================== 内部小工具 ====================

def _matched_alias(raw: Dict[str, Any], canonical: str) -> str:
    """返回该字段实际命中的来源字段名（用于判金额单位、写归一轨迹）。"""
    for alias in FIELD_MAP.get(canonical, [canonical]):
        if alias in raw and raw[alias] not in (None, ""):
            return alias
    return ""


def _money(raw: Dict[str, Any], canonical: str, trace: MapTrace, label: str = "") -> float:
    # 轨迹交给 to_yuan 一并写出（改名 + 换单位是一条），这里取值时不再记
    return to_yuan(pick(raw, canonical), source_field=_matched_alias(raw, canonical),
                   trace=trace, canonical=label or canonical)


def _time(raw: Dict[str, Any], canonical: str, trace: MapTrace, label: str = "") -> str:
    return format_time(pick(raw, canonical), source_field=_matched_alias(raw, canonical),
                       trace=trace, canonical=label or canonical)


def _number(raw: Dict[str, Any], canonical: str, trace: MapTrace, cast):
    value = pick(raw, canonical, trace=trace)
    if value in (None, ""):
        return cast(0)
    try:
        return cast(float(value))
    except (TypeError, ValueError):
        logger.warning(f"数值字段无法解析：{canonical}={value!r}")
        return cast(0)


def _int(raw: Dict[str, Any], canonical: str, trace: MapTrace) -> int:
    return _number(raw, canonical, trace, int)


def _float(raw: Dict[str, Any], canonical: str, trace: MapTrace) -> float:
    return _number(raw, canonical, trace, float)


def _str_list(raw: Dict[str, Any], canonical: str, trace: MapTrace) -> List[str]:
    """列表字段归一：真列表原样，逗号串拍开（上游偶尔把列表压成一行）。"""
    value = pick(raw, canonical, trace=trace)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    if isinstance(value, str) and value.strip():
        return [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
    return []
