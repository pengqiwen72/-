"""
mock 适配器的种子数据源。

职责只有一件事：把 `data/seed/*.json` 里**人写起来舒服**的结构，重组成
**上游系统真实返回**的形状 —— 用友/金蝶那类 ERP 的字段命名、SRM 的报价报文、
WMS 的库存报文。

为什么要在 mock 里多绕这一道
----------------------------
mock 的职责是模仿上游，不是模仿我们自己的标准结构。如果 mock 直接返回标准字段，
`integrations/normalize.py` 那一层在离线态就永远走不到 —— 它成了只为线上存在的
代码，demo 里没法验证，面试官也没法看。绕这一道之后，字段归一在离线态被
**真实地执行**：「matCode → material_code」「refPrice(分) → 单位＝5.60 元」
「orderStatus=30 → received(已入库)」这些轨迹是跑出来的，不是写死在输出里的。

四种上游形状的差异（也就是归一层要处理的全部内容）：
    ERP 物料     matCode / matName / specModel / matGroup / baseUnit / refPrice(分)
    SRM 供应商   supCode / supName / isNewSup / leadDays / onTimeRate(0~1) / defectRate
    SRM 报价     quotPrice(**分**) / minOrderQty —— 与「参考单价」是两套字段
    WMS 库存     onHandQty / frozenQty / safetyQty / avgOut30d；时间一律 epoch 毫秒

差异明细与映射规则见 `integrations/normalize.py` 的模块说明。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from store.seed_loader import resolve_offset
from utils.agent_logger import get_logger

logger = get_logger("mock_seed_source")

# data/seed 相对本文件：src/integrations/mock/seed_source.py -> ../../../data/seed
# （词法 parents，不写 .resolve()：见 utils/env_loader.py 的说明）
_SEED_DIR = Path(__file__).parents[3] / "data" / "seed"

#: 内部采购单状态 -> 上游 ERP 的两位状态码（归一层再翻回来）。
#: 两个方向都要在：mock 吐的是它，normalize 收的是它。
PO_STATUS_TO_ERP: Dict[str, str] = {
    "created": "10",
    "in_transit": "20",
    "received": "30",
    "cancelled": "40",
}


@lru_cache(maxsize=None)
def _read_json(filename: str) -> Dict[str, Any]:
    path = _SEED_DIR / filename
    if not path.exists():
        logger.warning(f"种子文件不存在：{path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # pragma: no cover
        logger.error(f"读取种子文件失败 {path}：{e}")
        return {}


def _yuan_to_cents(value: Any) -> int:
    """元 -> 分（上游金额字段一律用分；种子里写的是元，好读）。"""
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _epoch_ms(record: Dict[str, Any]) -> int:
    """相对偏移 -> 毫秒时间戳（上游 ERP/WMS 的 gmtCreate 就是这个格式）。"""
    moment = resolve_offset(record)
    return int(moment.timestamp() * 1000) if moment else 0


# ==================== ERP：物料主数据 ====================

def materials_raw() -> List[Dict[str, Any]]:
    """按 ERP 物料模块的字段形状返回物料清单。"""
    return [
        {
            "matCode": m.get("material_code", ""),
            "matName": m.get("material_name", ""),
            "specModel": m.get("spec", ""),
            "matGroup": m.get("category", ""),
            "baseUnit": m.get("unit", ""),
            # 参考单价上游给分
            "refPrice": _yuan_to_cents(m.get("unit_price_ref", 0)),
            "substituteCodes": list(m.get("substitutes") or []),
        }
        for m in _read_json("materials.json").get("materials", [])
    ]


# ==================== SRM：供应商与报价 ====================

def suppliers_raw() -> List[Dict[str, Any]]:
    """按 SRM 供应商档案的形状返回。"""
    return [
        {
            "supCode": s.get("supplier_code", ""),
            "supName": s.get("supplier_name", ""),
            "isNewSup": bool(s.get("is_new", False)),
            "leadDays": s.get("lead_time_days", 0),
            "onTimeRate": s.get("on_time_rate", 0.0),
            "defectRate": s.get("defect_rate", 0.0),
            "cooperationSince": s.get("cooperation_since", ""),
            "mainCategories": list(s.get("main_categories") or []),
        }
        for s in _read_json("suppliers.json").get("suppliers", [])
    ]


def quotes_raw() -> List[Dict[str, Any]]:
    """按 SRM 报价单的形状返回：quotPrice 给**分**，与 ERP 的 refPrice 是两套字段。"""
    return [
        {
            "matCode": q.get("material_code", ""),
            "supCode": q.get("supplier_code", ""),
            "quotPrice": _yuan_to_cents(q.get("unit_price", 0)),
            "minOrderQty": q.get("min_order_qty", 0),
            "validDays": q.get("valid_days", 30),
        }
        for q in _read_json("quotes.json").get("quotes", [])
    ]


# ==================== WMS：仓库 / 库存 / 收货 ====================

def warehouses_raw() -> List[Dict[str, Any]]:
    """按 WMS 基础档案的形状返回仓库清单。"""
    return [
        {
            "whCode": w.get("warehouse_code", ""),
            "whName": w.get("warehouse_name", ""),
            "city": w.get("city", ""),
            "addr": w.get("address", ""),
        }
        for w in _read_json("warehouses.json").get("warehouses", [])
    ]


def stock_raw() -> List[Dict[str, Any]]:
    """按 WMS 库存查询接口的形状返回库存快照。

    注意上游给的是**四个原始量**（在库/冻结/安全线/日均出库），没有「可用量」
    —— 可用量是业务口径（在库 − 冻结），由我们的模型算，不向下游要。
    """
    return [
        {
            "matCode": r.get("material_code", ""),
            "whCode": r.get("warehouse_code", ""),
            "onHandQty": r.get("on_hand", 0),
            "frozenQty": r.get("reserved", 0),
            "safetyQty": r.get("safety_stock", 0),
            "avgOut30d": r.get("avg_daily_out", 0),
        }
        for r in _read_json("stock.json").get("stock", [])
    ]


def receipts_raw() -> List[Dict[str, Any]]:
    """按 WMS 收货登记接口的形状返回收货单（历史单）。"""
    return [
        {
            "rcvNo": r.get("receipt_no", ""),
            "poNo": r.get("po_no", ""),
            "matCode": r.get("material_code", ""),
            "qtyPlan": r.get("qty_expected", 0),
            "qtyActual": r.get("qty_received", 0),
            "qtyBad": r.get("qty_defect", 0),
            "badType": r.get("defect_type", ""),
            "disposal": r.get("disposition", ""),
            "whCode": r.get("warehouse_code", ""),
            "supCode": r.get("supplier_code", ""),
            "checker": r.get("inspector", ""),
            "gmtCreate": _epoch_ms(r),
        }
        for r in _read_json("receipts.json").get("receipts", [])
    ]


# ==================== ERP：采购订单 ====================

def purchase_orders_raw() -> List[Dict[str, Any]]:
    """按 ERP 采购模块的形状返回采购订单（历史单）。"""
    return [
        {
            "poNo": p.get("po_no", ""),
            "prNo": p.get("request_id", ""),
            "matCode": p.get("material_code", ""),
            "qty": p.get("qty", 0),
            "unit": p.get("unit", ""),
            # 行单价与总额上游都给分
            "unitPrice": _yuan_to_cents(p.get("unit_price", 0)),
            "totalAmt": _yuan_to_cents(p.get("amount", 0)),
            "supCode": p.get("supplier_code", ""),
            "destWh": p.get("target_warehouse", ""),
            "orderStatus": PO_STATUS_TO_ERP.get(str(p.get("status", "")), ""),
            "gmtCreate": _epoch_ms(p),
            "operatorId": p.get("operator_id", ""),
        }
        for p in _read_json("purchase_orders.json").get("purchase_orders", [])
    ]


def reset_cache() -> None:
    """清空缓存（测试里改了种子文件后调用）。"""
    _read_json.cache_clear()
