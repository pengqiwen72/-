"""
主数据类 mock（物料 / 供应商 / 仓库）。

返回的是**上游报文的形状**，不是内部标准结构 —— 理由见 `seed_source.py`。
调用方拿到之后必须走 `integrations/normalize`。

三个类放一个文件，因为它们是同一类东西：只读的档案数据，没有状态、没有
写路径，读法都是「从种子 JSON 里筛」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from integrations.mock import chaos, seed_source
from integrations.ports import MaterialPort, SupplierPort, WarehousePort
from utils.agent_logger import get_logger

logger = get_logger("masterdata_mock")


def _norm(code: str) -> str:
    return (code or "").strip().upper()


# ==================== 物料（ERP 物料模块） ====================

class MockMaterialPort(MaterialPort):
    """读 `data/seed/materials.json`，按 ERP 物料模块的字段形状返回。"""

    def list_materials(self) -> List[Dict[str, Any]]:
        chaos.apply("material")
        return seed_source.materials_raw()

    def get_material(self, material_code: str) -> Optional[Dict[str, Any]]:
        chaos.apply("material")
        return _find_material(material_code)

    def search_materials(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        chaos.apply("material")

        kw = (keyword or "").strip().lower()
        rows = seed_source.materials_raw()
        if not kw:
            return rows[:limit]
        hit = [
            m for m in rows
            if kw in m["matCode"].lower() or kw in m["matName"].lower()
        ]
        logger.debug(f"[MOCK ERP] 物料搜索 {kw!r} 命中 {len(hit)} 条")
        return hit[:limit]

    def list_substitutes(self, material_code: str) -> List[Dict[str, Any]]:
        chaos.apply("material")

        base = _find_material(material_code)
        if base is None:
            return []
        codes = {_norm(c) for c in (base.get("substituteCodes") or [])}
        return [m for m in seed_source.materials_raw() if m["matCode"] in codes]


def _find_material(material_code: str) -> Optional[Dict[str, Any]]:
    """按编码找物料报文（不带故障注入 —— 供本文件内共用，避免注入被叠加两次）。"""
    key = _norm(material_code)
    if not key:
        return None
    for m in seed_source.materials_raw():
        if m["matCode"] == key:
            return m
    logger.info(f"[MOCK ERP] 未找到物料 {key}")
    return None


# ==================== 供应商与报价（SRM） ====================

class MockSupplierPort(SupplierPort):
    """读 `data/seed/suppliers.json` / `quotes.json`，按 SRM 的形状返回。"""

    def list_suppliers(self) -> List[Dict[str, Any]]:
        chaos.apply("supplier")
        return seed_source.suppliers_raw()

    def get_supplier(self, supplier_code: str) -> Optional[Dict[str, Any]]:
        chaos.apply("supplier")

        key = _norm(supplier_code)
        if not key:
            return None
        for s in seed_source.suppliers_raw():
            if s["supCode"] == key:
                return s
        logger.info(f"[MOCK SRM] 未找到供应商 {key}")
        return None

    def list_supplier_quotes(self, material_code: str) -> List[Dict[str, Any]]:
        chaos.apply("supplier")

        key = _norm(material_code)
        if not key:
            return []
        hit = [q for q in seed_source.quotes_raw() if q["matCode"] == key]
        logger.debug(f"[MOCK SRM] 物料 {key} 命中 {len(hit)} 家报价")
        return hit


# ==================== 仓库（WMS 基础档案） ====================

class MockWarehousePort(WarehousePort):
    """读 `data/seed/warehouses.json`，按 WMS 基础档案的形状返回。"""

    def list_warehouses(self) -> List[Dict[str, Any]]:
        chaos.apply("warehouse")
        return seed_source.warehouses_raw()
