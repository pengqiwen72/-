"""
把 `data/seed/*.json` 灌进 LangGraph Store。

为什么需要它
------------
图里的节点全部通过 store 读业务数据（物料、供应商、仓库、库存、采购订单…）。
离线跑 demo 时 store 是空的，于是每个节点都返回空值 —— 「能跑通」但看不到
任何业务产出。这个 loader 把**虚构但自洽**的种子数据灌进去，让全链路有真实
内容可处理。

种子不是「随手编几行」：跨文件有一组硬约束（外键、报价对、金额自洽、收货单
指向），`verify_seed_data()` 在每次加载前全量核对，不通过就拒绝加载 ——
demo 数字对不上比跑不起来更伤，见该函数的说明。

时间为什么用相对偏移
--------------------
种子数据里写的是 `days_ago: 9` / `hours_ago: 5` 而不是 `2026-03-15 15:40`。
如果写死绝对时间，「在途订单」「近 30 天出库」这类口径在你 clone 仓库一个月后
就会全部过期，演示直接失去意义。改成加载时按当前时间回推，
保证「有几单在途、最近有出库」在任何时候都成立。

用法
----
    from store.seed_loader import load_seed_data, build_knowledge_graph
    load_seed_data(store)            # 幂等，重复调用只是覆盖同样的键
    build_knowledge_graph(graph)     # 同一批 JSON 投影成知识图谱（见该函数说明）

store（LangGraph Store）与 graph（知识图谱）是**两个消费者、一份事实**：
前者供子图读写业务数据，后者供多跳查询，投影规则各自在加载/建图时明确写死，
JSON 始终是唯一事实源。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.store.base import BaseStore

from utils.agent_logger import get_logger

logger = get_logger("seed_loader")

# data/seed 相对本文件：src/store/seed_loader.py -> ../../data/seed
# ⚠️ 不要改回 `.resolve()`：Windows 上它会调 `os.getcwd()`，而本模块是在演示
# 工作台**首次请求**里才 import 的 —— blockbuster 会当场把请求打成 500（真踩过）。
# 完整说明见 utils/env_loader.py；同理，`os.path.abspath` 只对绝对路径安全。
_SEED_DIR = Path(__file__).parents[2] / "data" / "seed"

# 东八区偏移（考虑夏令时不需要，中国不使用夏令时）
_TZ_CHINA = timezone(timedelta(hours=8))

# 时间偏移字段 -> 对应的 timedelta 参数名
_OFFSET_UNITS = {
    "minutes_ago": "minutes",
    "hours_ago": "hours",
    "days_ago": "days",
}


# ==================== 工具 ====================

def _read_json(filename: str) -> Dict[str, Any]:
    path = _SEED_DIR / filename
    if not path.exists():
        logger.warning(f"种子文件不存在，跳过：{path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取种子文件失败 {path}: {e}")
        return {}


def resolve_offset(record: Dict[str, Any]) -> Optional[datetime]:
    """把记录里的相对时间描述解成一个具体时刻（东八区）。

    支持 `minutes_ago` / `hours_ago` / `days_ago`（可带小数），
    以及可选的 `at`（"HH:MM" 或 "HH:MM:SS"）用来把时刻钉在某个整点附近。

    返回 None 表示这条记录没有时间信息（有些数据本来就不需要）。
    """
    now = datetime.now(_TZ_CHINA)

    delta = timedelta(0)
    found = False
    for field, unit in _OFFSET_UNITS.items():
        value = record.get(field)
        if value is None:
            continue
        delta += timedelta(**{unit: float(value)})
        found = True

    if not found:
        return None

    moment = now - delta

    # `at` 把时刻钉到当天的指定时分，避免「9 天前」落在当前这一刻导致
    # 同一批数据的先后顺序随运行时间漂移
    at = record.get("at")
    if at:
        try:
            parts = [int(p) for p in str(at).split(":")]
            while len(parts) < 3:
                parts.append(0)
            moment = moment.replace(hour=parts[0], minute=parts[1],
                                    second=parts[2], microsecond=0)
        except (ValueError, TypeError):
            logger.warning(f"无法解析时间点 at={at!r}，忽略")

    return moment


def to_timestamp(record: Dict[str, Any]) -> int:
    """转成 UTC 秒级时间戳（store 里存的格式，下游用 timestamp2datetime 还原）。"""
    moment = resolve_offset(record)
    return int(moment.timestamp()) if moment else 0


def to_yyyymmdd(record: Dict[str, Any]) -> str:
    """转成 YYYYMMDD，供 store 的区间过滤使用。"""
    moment = resolve_offset(record)
    return moment.strftime("%Y%m%d") if moment else ""


# ==================== 装载 ====================
# 命名空间约定（查询方按这个取数，逐一对应）：
#   ("material",)                  key=material_code
#   ("supplier",)                  key=supplier_code
#   ("warehouse",)                 key=warehouse_code
#   ("stock", material_code)       key=warehouse_code   —— 按物料看多仓分布
#   ("stock_wh", warehouse_code)   key=material_code    —— 按仓库盘点是同一份数据的另一个视角
#   ("quote", material_code)       key=supplier_code    —— 图谱 SUPPLIES 边的来源
#   ("purchase_order",)            key=po_no
#   ("receipt",)                   key=receipt_no
#   ("operator",)                  key=operator_id
#   ("demand",)                    key=demand_id
#   ("bom",)                       key=bom_id
#   ("contract",)                  key=contract_no
#   ("ticket",)                    key=ticket_no


def _put_all(store: BaseStore, namespace: tuple, key_field: str,
             records: List[Dict[str, Any]]) -> int:
    """把一批记录写进某个命名空间；缺主键的记录跳过（校验层已保证不会发生）。"""
    count = 0
    for rec in records:
        key = str(rec.get(key_field) or "")
        if not key:
            continue
        store.put(namespace, key, rec)
        count += 1
    return count


def _load_materials(store: BaseStore) -> int:
    return _put_all(store, ("material",), "material_code",
                    _read_json("materials.json").get("materials", []))


def _load_suppliers(store: BaseStore) -> int:
    return _put_all(store, ("supplier",), "supplier_code",
                    _read_json("suppliers.json").get("suppliers", []))


def _load_warehouses(store: BaseStore) -> int:
    return _put_all(store, ("warehouse",), "warehouse_code",
                    _read_json("warehouses.json").get("warehouses", []))


def _load_scm_stock(store: BaseStore) -> int:
    """库存写两份命名空间（按物料 / 按仓库）。

    两个视角都是高频查询，各写一份换 O(1) 的取数；120 行的数据量下这点冗余
    可以忽略，换来的是查询侧永远不需要全量扫描。
    """
    count = 0
    for row in _read_json("stock.json").get("stock", []):
        material_code = str(row.get("material_code") or "")
        warehouse_code = str(row.get("warehouse_code") or "")
        if not material_code or not warehouse_code:
            continue
        store.put(("stock", material_code), warehouse_code, row)
        store.put(("stock_wh", warehouse_code), material_code, row)
        count += 1
    return count


def _load_quotes(store: BaseStore) -> int:
    """报价按物料分命名空间：查「这个物料有哪几家报价」是比价链路的入口。"""
    count = 0
    for q in _read_json("quotes.json").get("quotes", []):
        material_code = str(q.get("material_code") or "")
        supplier_code = str(q.get("supplier_code") or "")
        if not material_code or not supplier_code:
            continue
        store.put(("quote", material_code), supplier_code, q)
        count += 1
    return count


def _load_scm_purchase_orders(store: BaseStore) -> int:
    return _put_all(store, ("purchase_order",), "po_no",
                    _read_json("purchase_orders.json").get("purchase_orders", []))


def _load_receipts(store: BaseStore) -> int:
    return _put_all(store, ("receipt",), "receipt_no",
                    _read_json("receipts.json").get("receipts", []))


def _load_operators(store: BaseStore) -> int:
    return _put_all(store, ("operator",), "operator_id",
                    _read_json("operators.json").get("operators", []))


def _load_demand(store: BaseStore) -> int:
    return _put_all(store, ("demand",), "demand_id",
                    _read_json("demand.json").get("demands", []))


def _load_bom(store: BaseStore) -> int:
    return _put_all(store, ("bom",), "bom_id",
                    _read_json("bom.json").get("bom", []))


def _load_contracts(store: BaseStore) -> int:
    return _put_all(store, ("contract",), "contract_no",
                    _read_json("contracts.json").get("contracts", []))


def _load_tickets(store: BaseStore) -> int:
    return _put_all(store, ("ticket",), "ticket_no",
                    _read_json("tickets.json").get("tickets", []))


def _count_policies(filename: str) -> int:
    """策略文件不进 store：策略引擎（integrations/policy.py）直接读文件，
    这里只统计条数进加载报告。"""
    return len(_read_json(filename).get("policies", []))


# ==================== 供应链域：完整性校验 ====================

def _collect_codes(records: List[Dict[str, Any]], key: str, label: str,
                   errors: List[str]) -> set:
    """收集主键并检出重复/缺失 —— 编码重复会让 store 静默覆盖，必须拦下。"""
    codes: set = set()
    for rec in records:
        code = str(rec.get(key) or "")
        if not code:
            errors.append(f"{label}记录缺少 {key}：{rec!r}")
            continue
        if code in codes:
            errors.append(f"{label}编码重复：{code}（会互相覆盖）")
        codes.add(code)
    return codes


def verify_seed_data() -> List[str]:
    """跨文件完整性校验，返回错误清单（空列表 = 全部通过）。

    为什么要有它
    ------------
    种子数据是演示的**唯一事实源**：图谱的边、策略的输入、场景的断言全部从
    这里长出来。一处编码写错（替代料指向不存在的物料、收货单挂到不存在的
    采购单）不会当场报错，而是让某条链路静默少答一半。与其在演示时排查，
    不如每次加载前全量核对一遍 —— 数据量就这么大，校验成本可以忽略。

    校验口径（与 models/scm_models.py 的字段语义一一对应）：
      1. 主键唯一：物料/供应商/仓库/采购单/收货单/操作员等编码不重复、不为空；
      2. 外键存在：库存、报价、采购单、收货单、需求、BOM、合同、工单引用的编码都能找到；
      3. 图边来源：替代料编码必须存在（SUBSTITUTES 边不许悬空）；报价对即
         SUPPLIES 边，物料×供应商都必须存在；
      4. 金额自洽：采购单 amount == qty × unit_price（±0.01，留浮点余量）；
      5. 数量关系：库存 reserved ≤ on_hand（可用量不能为负）；收货单 po_no
         必须指向存在的采购单、物料与采购单一致、不良数 ≤ 实收数；
      6. 策略文件：规则字段齐全，且必须包含一条空条件 {} 的兜底规则 ——
         没有兜底时，规则引擎会对某些申请返回「无规则命中」，那是静默失配。
    """
    errors: List[str] = []

    materials = _read_json("materials.json").get("materials", [])
    suppliers = _read_json("suppliers.json").get("suppliers", [])
    warehouses = _read_json("warehouses.json").get("warehouses", [])
    stock = _read_json("stock.json").get("stock", [])
    quotes = _read_json("quotes.json").get("quotes", [])
    orders = _read_json("purchase_orders.json").get("purchase_orders", [])
    receipts = _read_json("receipts.json").get("receipts", [])
    demands = _read_json("demand.json").get("demands", [])
    boms = _read_json("bom.json").get("bom", [])
    contracts = _read_json("contracts.json").get("contracts", [])
    tickets = _read_json("tickets.json").get("tickets", [])

    mat_codes = _collect_codes(materials, "material_code", "物料", errors)
    sup_codes = _collect_codes(suppliers, "supplier_code", "供应商", errors)
    wh_codes = _collect_codes(warehouses, "warehouse_code", "仓库", errors)
    po_codes = _collect_codes(orders, "po_no", "采购单", errors)
    _collect_codes(receipts, "receipt_no", "收货单", errors)
    _collect_codes(_read_json("operators.json").get("operators", []),
                   "operator_id", "操作员", errors)

    po_by_no = {str(o.get("po_no")): o for o in orders}

    # ---- 替代料：图谱 SUBSTITUTES 边的目标必须存在 ----
    for m in materials:
        for sub in m.get("substitutes") or []:
            if sub not in mat_codes:
                errors.append(
                    f"物料 {m.get('material_code')} 的替代料 {sub} 不在物料主数据里"
                )

    # ---- 库存：外键 + 数量关系 ----
    for row in stock:
        code = row.get("material_code")
        wh = row.get("warehouse_code")
        if code not in mat_codes:
            errors.append(f"库存 {code}@{wh} 引用了不存在的物料")
        if wh not in wh_codes:
            errors.append(f"库存 {code}@{wh} 引用了不存在的仓库")
        on_hand = int(row.get("on_hand", 0))
        reserved = int(row.get("reserved", 0))
        if on_hand < 0 or reserved < 0:
            errors.append(f"库存 {code}@{wh} 出现负数：on_hand={on_hand} reserved={reserved}")
        elif reserved > on_hand:
            errors.append(
                f"库存 {code}@{wh} 的预留（{reserved}）超过在库（{on_hand}），可用量会变成负数"
            )

    # ---- 报价：图谱 SUPPLIES 边的来源，物料×供应商都必须存在 ----
    for q in quotes:
        code = q.get("material_code")
        sup = q.get("supplier_code")
        if code not in mat_codes:
            errors.append(f"报价引用了不存在的物料：{code}")
        if sup not in sup_codes:
            errors.append(f"报价引用了不存在的供应商：{sup}")
        if float(q.get("unit_price", 0)) <= 0:
            errors.append(f"报价单价非正：{code}×{sup} = {q.get('unit_price')}")

    # ---- 采购单：外键 + 金额自洽 ----
    for o in orders:
        po_no = o.get("po_no")
        if o.get("material_code") not in mat_codes:
            errors.append(f"采购单 {po_no} 引用了不存在的物料：{o.get('material_code')}")
        if o.get("supplier_code") not in sup_codes:
            errors.append(f"采购单 {po_no} 引用了不存在的供应商：{o.get('supplier_code')}")
        if o.get("target_warehouse") not in wh_codes:
            errors.append(f"采购单 {po_no} 引用了不存在的仓库：{o.get('target_warehouse')}")
        expected = float(o.get("qty", 0)) * float(o.get("unit_price", 0))
        if abs(float(o.get("amount", 0)) - expected) > 0.01:
            errors.append(
                f"采购单 {po_no} 金额不自洽：amount={o.get('amount')}，"
                f"qty×unit_price={expected:.2f}"
            )

    # ---- 收货单：必须挂在存在的采购单上，且物料一致、不良数合理 ----
    for r in receipts:
        po_no = r.get("po_no")
        po = po_by_no.get(str(po_no))
        if po is None:
            errors.append(f"收货单 {r.get('receipt_no')} 挂在不存在的采购单上：{po_no}")
        elif po.get("material_code") != r.get("material_code"):
            errors.append(
                f"收货单 {r.get('receipt_no')} 的物料（{r.get('material_code')}）"
                f"与采购单 {po_no}（{po.get('material_code')}）不一致"
            )
        received = int(r.get("qty_received", 0))
        defect = int(r.get("qty_defect", 0))
        if defect < 0 or received < 0:
            errors.append(f"收货单 {r.get('receipt_no')} 出现负数数量")
        elif defect > received:
            errors.append(
                f"收货单 {r.get('receipt_no')} 不良数（{defect}）超过实收（{received}）"
            )

    # ---- 需求 / BOM / 合同 / 工单：外键 ----
    for d in demands:
        if d.get("material_code") not in mat_codes:
            errors.append(f"需求 {d.get('demand_id')} 引用了不存在的物料：{d.get('material_code')}")
    for b in boms:
        for field in ("parent_code", "component_code"):
            if b.get(field) not in mat_codes:
                errors.append(f"BOM {b.get('bom_id')} 的 {field}={b.get(field)} 不在物料主数据里")
        if float(b.get("qty_per", 0)) <= 0:
            errors.append(f"BOM {b.get('bom_id')} 的单台用量非正：{b.get('qty_per')}")
    for c in contracts:
        if c.get("supplier_code") not in sup_codes:
            errors.append(f"合同 {c.get('contract_no')} 引用了不存在的供应商：{c.get('supplier_code')}")
    for t in tickets:
        if t.get("po_no") and str(t["po_no"]) not in po_codes:
            errors.append(f"工单 {t.get('ticket_no')} 挂在不存在的采购单上：{t['po_no']}")
        if t.get("material_code") and t["material_code"] not in mat_codes:
            errors.append(f"工单 {t.get('ticket_no')} 引用了不存在的物料：{t['material_code']}")

    # ---- 策略文件：结构 + 兜底 ----
    for filename in ("purchase_policy.json", "quality_policy.json"):
        policies = _read_json(filename).get("policies", [])
        if not policies:
            errors.append(f"策略文件 {filename} 没有规则")
            continue
        has_fallback = False
        for p in policies:
            if not (p.get("conditions") or {}):
                has_fallback = True
            for field in ("policy_id", "priority", "title", "action"):
                if field not in p:
                    errors.append(f"{filename} 的规则缺少字段 {field}：{p.get('policy_id', '?')}")
        if not has_fallback:
            errors.append(f"{filename} 缺少空条件 {{}} 的兜底规则")

    return errors


# ==================== 知识图谱投影 ====================

def build_knowledge_graph(graph) -> Dict[str, Any]:
    """把种子 JSON 投影成知识图谱（`kg/store.py::get_graph_store` 首次调用时建图）。

    投影规则
    --------
      * **节点**：物料 / 供应商 / 仓库 / 库存快照 / 采购单 / 收货单 / 策略规则。
        属性只取**查询用得到的字段** —— 种子里 `days_ago` 这类加载期字段
        不进图，否则图与 JSON 会各自偷偷长出一份真相。
      * **边**：只建会被多跳遍历用到的关系（口径见 `kg/schema.py`）：
        报价对 → SUPPLIES，登记的替代料 → SUBSTITUTES，库存快照 → 物料/仓库，
        采购单 → 物料/供应商，收货单 → 物料/仓库。POLICY 只有节点没有边。
      * **库存口径**（qty_available / below_safety / turnover_days）由
        `StockItem` 计算后写进节点属性 —— 口径只在模型里定义一次，
        图查询读到的值与库存子图聚合出的值必然一致。

    写入顺序：**先全部节点、后全部边**。neo4j 的 `upsert_edge` 是 MATCH 语义，
    端点不存在时边会被静默丢掉；先建点后建边让两种后端的行为一致。
    幂等：重复调用覆盖同样的键（与 `load_seed_data` 同一约定）。

    数据合法性由 `verify_seed_data()` 前置保证（外键、替代料存在、报价对有效），
    这里不再做防御性判空 —— 有一条校验漏网就会在图上留下悬空边，
    而那是校验层的问题，不该在这里被悄悄吞掉。
    """
    # 函数内 import：seed_loader 被 kg 层回调，模块级 import kg 会把
    # 「kg -> store.seed_loader -> kg」拧成一个 import 环
    from kg.schema import EdgeKind, NodeKind, node_id, stock_item_key
    from models.scm_models import ReceiptPlan, StockItem

    materials = _read_json("materials.json").get("materials", [])
    suppliers = _read_json("suppliers.json").get("suppliers", [])
    warehouses = _read_json("warehouses.json").get("warehouses", [])
    stock = _read_json("stock.json").get("stock", [])
    quotes = _read_json("quotes.json").get("quotes", [])
    orders = _read_json("purchase_orders.json").get("purchase_orders", [])
    receipts = _read_json("receipts.json").get("receipts", [])

    stats: Dict[str, Any] = {"node": 0, "edge": 0, "by_kind": {}, "by_edge": {}}

    def add_node(kind: NodeKind, key: str, props: Dict[str, Any]) -> None:
        graph.upsert_node(kind, key, props)
        stats["node"] += 1
        stats["by_kind"][kind.value] = stats["by_kind"].get(kind.value, 0) + 1

    def add_edge(src: str, dst: str, kind: EdgeKind,
                 props: Optional[Dict[str, Any]] = None) -> None:
        graph.upsert_edge(src, dst, kind, props or {})
        stats["edge"] += 1
        stats["by_edge"][kind.value] = stats["by_edge"].get(kind.value, 0) + 1

    # ---- 节点 ----
    for m in materials:
        add_node(NodeKind.MATERIAL, m["material_code"], {
            "material_name": m.get("material_name", ""),
            "spec": m.get("spec", ""),
            "category": m.get("category", ""),
            "unit": m.get("unit", ""),
            "unit_price_ref": m.get("unit_price_ref", 0.0),
        })

    for s in suppliers:
        add_node(NodeKind.SUPPLIER, s["supplier_code"], {
            "supplier_name": s.get("supplier_name", ""),
            "is_new": bool(s.get("is_new", False)),
            "lead_time_days": s.get("lead_time_days", 0),
            "on_time_rate": s.get("on_time_rate", 0.0),
            "defect_rate": s.get("defect_rate", 0.0),
            "cooperation_since": s.get("cooperation_since", ""),
            "main_categories": list(s.get("main_categories") or []),
        })

    for w in warehouses:
        add_node(NodeKind.WAREHOUSE, w["warehouse_code"], {
            "warehouse_name": w.get("warehouse_name", ""),
            "city": w.get("city", ""),
            "address": w.get("address", ""),
        })

    for row in stock:
        item = StockItem(**row)
        add_node(NodeKind.STOCK_ITEM,
                 stock_item_key(item.material_code, item.warehouse_code), {
                     "material_code": item.material_code,
                     "warehouse_code": item.warehouse_code,
                     "on_hand": item.on_hand,
                     "reserved": item.reserved,
                     "safety_stock": item.safety_stock,
                     "avg_daily_out": item.avg_daily_out,
                     "qty_available": item.qty_available,
                     "below_safety": item.below_safety,
                     "turnover_days": item.turnover_days,
                 })

    for o in orders:
        add_node(NodeKind.PO, o["po_no"], {
            "request_id": o.get("request_id", ""),
            "material_code": o.get("material_code", ""),
            "qty": o.get("qty", 0),
            "unit": o.get("unit", ""),
            "unit_price": o.get("unit_price", 0.0),
            "amount": o.get("amount", 0.0),
            "supplier_code": o.get("supplier_code", ""),
            "target_warehouse": o.get("target_warehouse", ""),
            "status": o.get("status", ""),
            "status_text": o.get("status_text", ""),
            "operator_id": o.get("operator_id", ""),
        })

    for r in receipts:
        add_node(NodeKind.RECEIPT, r["receipt_no"], {
            "po_no": r.get("po_no", ""),
            "material_code": r.get("material_code", ""),
            "qty_expected": r.get("qty_expected", 0),
            "qty_received": r.get("qty_received", 0),
            "qty_defect": r.get("qty_defect", 0),
            "defect_rate": ReceiptPlan.compute_defect_rate(
                int(r.get("qty_received", 0)), int(r.get("qty_defect", 0))),
            "defect_type": r.get("defect_type", ""),
            "disposition": r.get("disposition", ""),
            "warehouse_code": r.get("warehouse_code", ""),
            "supplier_code": r.get("supplier_code", ""),
            "inspector": r.get("inspector", ""),
        })

    for filename in ("purchase_policy.json", "quality_policy.json"):
        for p in _read_json(filename).get("policies", []):
            add_node(NodeKind.POLICY, p["policy_id"], {
                "priority": p.get("priority", 0),
                "title": p.get("title", ""),
                "action": p.get("action", ""),
                # conditions 是嵌套 dict，neo4j 属性只支持标量/列表 ——
                # 序列化成 JSON 字符串，两边后端都能存
                "conditions": json.dumps(p.get("conditions") or {},
                                         ensure_ascii=False),
            })

    # ---- 边 ----
    # 替代料：互替关系在种子里两边都登记，这里原样成边（谁登记谁负责）
    for m in materials:
        for sub in m.get("substitutes") or []:
            add_edge(node_id(NodeKind.MATERIAL, m["material_code"]),
                     node_id(NodeKind.MATERIAL, sub), EdgeKind.SUBSTITUTES)

    # 报价对 -> SUPPLIES 边（有报价才叫「供得了」；边属性沿用报价单字段）
    for q in quotes:
        add_edge(node_id(NodeKind.SUPPLIER, q["supplier_code"]),
                 node_id(NodeKind.MATERIAL, q["material_code"]),
                 EdgeKind.SUPPLIES, {
                     "unit_price": q.get("unit_price", 0.0),
                     "min_order_qty": q.get("min_order_qty", 0),
                     "valid_days": q.get("valid_days", 0),
                 })

    # 库存快照 -> 物料 / 仓库
    for row in stock:
        sid = node_id(NodeKind.STOCK_ITEM,
                      stock_item_key(row["material_code"], row["warehouse_code"]))
        add_edge(sid, node_id(NodeKind.MATERIAL, row["material_code"]),
                 EdgeKind.IS_MATERIAL)
        add_edge(sid, node_id(NodeKind.WAREHOUSE, row["warehouse_code"]),
                 EdgeKind.STOCKED_AT)

    # 采购单 -> 物料 / 供应商（target_warehouse 是属性，见 kg/schema.py）
    for o in orders:
        pid = node_id(NodeKind.PO, o["po_no"])
        add_edge(pid, node_id(NodeKind.MATERIAL, o["material_code"]),
                 EdgeKind.IS_MATERIAL)
        add_edge(pid, node_id(NodeKind.SUPPLIER, o["supplier_code"]),
                 EdgeKind.SOURCED_FROM)

    # 收货单 -> 物料 / 仓库
    for r in receipts:
        rid = node_id(NodeKind.RECEIPT, r["receipt_no"])
        add_edge(rid, node_id(NodeKind.MATERIAL, r["material_code"]),
                 EdgeKind.IS_MATERIAL)
        add_edge(rid, node_id(NodeKind.WAREHOUSE, r["warehouse_code"]),
                 EdgeKind.RECEIVED_AT)

    logger.info(
        f"知识图谱投影完成：{stats['node']} 节点 / {stats['edge']} 边"
        f"（节点 {stats['by_kind']}，边 {stats['by_edge']}）"
    )
    return stats


# ==================== 对外入口 ====================

def load_seed_data(store: Optional[BaseStore] = None,
                   verify: bool = True) -> Dict[str, int]:
    """把全部种子数据灌进 store，返回各类数据的写入条数。

    幂等：同一份种子重复加载只是覆盖同样的键，不会产生重复记录。
    `verify=True`（默认）先在加载前做跨文件完整性校验，不通过直接抛 ——
    加载一份自相矛盾的数据，比拒绝加载危险得多（见 verify_seed_data 的说明）。
    """
    if store is None:
        from store.store_provider import get_fallback_store

        store = get_fallback_store()

    if verify:
        errors = verify_seed_data()
        if errors:
            for e in errors:
                logger.error(f"[种子校验] {e}")
            raise RuntimeError(
                f"种子数据校验未通过（{len(errors)} 项），拒绝加载；逐条错误见日志"
            )

    stats = {
        # ---- 供应链采购仓储域 ----
        "material": _load_materials(store),
        "supplier": _load_suppliers(store),
        "warehouse": _load_warehouses(store),
        "stock": _load_scm_stock(store),
        "quote": _load_quotes(store),
        "purchase_order": _load_scm_purchase_orders(store),
        "receipt": _load_receipts(store),
        "operator": _load_operators(store),
        "demand": _load_demand(store),
        "bom": _load_bom(store),
        "contract": _load_contracts(store),
        "ticket": _load_tickets(store),
        # 策略文件不进 store，只统计条数
        "purchase_policy": _count_policies("purchase_policy.json"),
        "quality_policy": _count_policies("quality_policy.json"),
    }

    total = sum(stats.values())
    logger.info(
        "种子数据加载完成："
        + "，".join(f"{k}={v}" for k, v in stats.items())
        + f"，共 {total} 条"
    )
    return stats


if __name__ == "__main__":  # pragma: no cover - 手动排查用
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).parents[1]))
    print(load_seed_data())
