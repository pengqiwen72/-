"""
库存 mock（仓储 WMS）。

返回空列表表示「这个 SKU 在 WMS 里没有记录」，是**业务上的空结果**，
由调用方决定怎么表述（多数情况下对应「该仓不备此货」）。与接口异常区分开。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from integrations.mock import chaos, seed_source
from integrations.ports import InventoryPort
from utils.agent_logger import get_logger

logger = get_logger("inventory_mock")


class MockInventoryPort(InventoryPort):
    """读 `data/seed/inventory.json`，按 WMS 的字段形状返回。"""

    def get_stock(self, sku_ids: List[str], warehouse: Optional[str] = None) -> List[Dict[str, Any]]:
        chaos.apply("inventory")

        wanted = {s.strip() for s in (sku_ids or []) if s and s.strip()}
        if not wanted:
            return []

        rows = []
        for row in seed_source.inventory_raw():
            if row.get("skuCode") not in wanted:
                continue
            if warehouse and row.get("warehouseName") != warehouse:
                continue
            rows.append(row)

        logger.debug(f"[MOCK 仓储] 查询 {sorted(wanted)} 命中 {len(rows)} 行")
        return rows
