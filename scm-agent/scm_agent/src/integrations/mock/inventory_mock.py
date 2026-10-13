"""
库存 mock（WMS）。

按 WMS 库存查询接口的形状返回：matCode / whCode / onHandQty / frozenQty /
safetyQty / avgOut30d —— **上游不给「可用量」**，可用量（在库 − 冻结）是业务
口径，由 `models/scm_models.py::StockItem` 的属性和归一后的模型计算，
不向下游要一个我们已经会算的数字。
"""

from __future__ import annotations

from typing import Any, Dict, List

from integrations.mock import chaos, seed_source
from integrations.ports import InventoryPort
from utils.agent_logger import get_logger

logger = get_logger("inventory_mock")


def _norm(code: str) -> str:
    return (code or "").strip().upper()


class MockInventoryPort(InventoryPort):
    """读 `data/seed/stock.json`（120 个 物料 × 仓库 库存点）。"""

    def list_stock(self) -> List[Dict[str, Any]]:
        chaos.apply("inventory")
        return seed_source.stock_raw()

    def list_stock_by_material(self, material_code: str) -> List[Dict[str, Any]]:
        chaos.apply("inventory")

        key = _norm(material_code)
        if not key:
            return []
        hit = [r for r in seed_source.stock_raw() if r["matCode"] == key]
        logger.debug(f"[MOCK WMS] 物料 {key} 命中 {len(hit)} 个库存点")
        return hit

    def list_stock_by_warehouse(self, warehouse_code: str) -> List[Dict[str, Any]]:
        chaos.apply("inventory")

        key = _norm(warehouse_code)
        if not key:
            return []
        hit = [r for r in seed_source.stock_raw() if r["whCode"] == key]
        logger.debug(f"[MOCK WMS] 仓库 {key} 命中 {len(hit)} 个库存点")
        return hit
