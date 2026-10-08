"""
查询类子图共用的实体定位。

库存（`sub_inventory`）与供应商（`sub_supplier`）两条链路都是**只读查询**，
不该为了一句「M100023 还有多少库存」去调一次大模型 —— 定不了位顶多追问一句，
定位错了却会把别的物料的数据答成它的（比不答更糟）。所以这两条链路的
物料/仓库定位用同一套确定性规则：

    编码直命中（M\\d{6} / WH\\d{2}） > 名称切段匹配

名称匹配沿用离线合成器（`llm/offline/synth_scm.py`）的切段打分：主数据全名是
「冷轧钢板 1.2mm」而用户只说「冷轧钢板」，整名包含匹配永远命中不了；
按空白切段后「任一段出现在文本里」即算命中，命中段总长更大者优先。
两边（抽取链路与查询链路）用同一套口径，才不会出现「同一个说法，
采购链路认得、库存链路认不得」。

**不猜**：匹配不到就返回空，由调用方追问 —— 这里的每一次「差不多就行」
都会变成一次答非所问。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

#: 编码形态：与种子数据、图谱实体链接共用同一套口径
_RE_MATERIAL_CODE = re.compile(r"\bM\d{6}\b")
_RE_WAREHOUSE_CODE = re.compile(r"\bWH\d{2}\b")


def match_by_name(candidates: List[dict], text: str,
                  code_field: str, name_field: str) -> Tuple[str, str]:
    """名称切段匹配：返回 (code, name)，未命中返回 ("", "")。"""
    best: Tuple[str, str] = ("", "")
    best_score = 0
    for item in candidates:
        name = str(item.get(name_field) or "")
        if not name:
            continue
        score = sum(len(seg) for seg in name.split() if len(seg) >= 2 and seg in text)
        if score > best_score:
            best_score = score
            best = (str(item.get(code_field) or ""), name)
    return best


def resolve_material(text: str, materials) -> Optional[object]:
    """把口语说法定位到一个物料（返回 MaterialInfo，定位不到返回 None）。"""
    if not text:
        return None

    hit = _RE_MATERIAL_CODE.search(text)
    if hit:
        code = hit.group(0)
        for m in materials or []:
            if m.material_code == code:
                return m
        return None  # 编码是真的、但主数据里没有：不能降级成「随便找一个」

    candidates = [
        {"material_code": m.material_code, "material_name": m.material_name}
        for m in materials or []
    ]
    code, _ = match_by_name(candidates, text, "material_code", "material_name")
    if not code:
        return None
    for m in materials or []:
        if m.material_code == code:
            return m
    return None


def resolve_warehouse(text: str, warehouses) -> Optional[object]:
    """把口语说法定位到一个仓库（返回 WarehouseInfo，定位不到返回 None）。"""
    if not text:
        return None

    hit = _RE_WAREHOUSE_CODE.search(text)
    if hit:
        code = hit.group(0)
        for w in warehouses or []:
            if w.warehouse_code == code:
                return w
        return None

    candidates = [
        {"warehouse_code": w.warehouse_code, "warehouse_name": w.warehouse_name}
        for w in warehouses or []
    ]
    code, _ = match_by_name(candidates, text, "warehouse_code", "warehouse_name")
    if not code:
        return None
    for w in warehouses or []:
        if w.warehouse_code == code:
            return w
    return None


__all__ = ["match_by_name", "resolve_material", "resolve_warehouse"]
