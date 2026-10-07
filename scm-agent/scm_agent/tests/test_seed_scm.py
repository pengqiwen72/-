"""
供应链种子数据与离线要素提取合成器的门禁测试。

这个文件守两类东西
------------------
1. **种子数据的剧情锚点**。演示故事线（替代料、比价、调拨、审批、质检复核）
   全部依赖种子里几组特定的数字成立：M100023 有替代料 M100031 且 WH02 有货、
   三家报价价差 ≥8%、M400301 一仓缺一仓富、8000×5.60=44800 且 S012 是新供应商、
   收货 30/920=3.26% 超 2% 阈值。这些数字一旦被谁「顺手调顺眼」，场景就会静默
   失效 —— 不是报错，是演示时才发现答不出来。所以逐个钉死。

2. **离线合成器的数据驱动性**。要素提取必须从提示词里的候选块和用户原话里取数，
   不能返回常量、更不能把 `{example_json}` 里的示例实例漏进结果 —— 后者尤其隐蔽：
   示例实例恰好也是 M400301@WH02，一旦漏进来，错误结果看起来完全正确。

`verify_seed_data()` 的正向通过只说明「没查出问题」，也可能是因为压根没查。
最后一个用例故意喂一份坏数据，确认这道闸真的会拦人。
"""

from __future__ import annotations

import json

import pytest


def _seed(filename: str):
    """直读种子文件（与 loader 同一份实现，避免测试里重写一遍编码处理）。"""
    from store.seed_loader import _read_json

    return _read_json(filename)


def _by(records, field):
    return {r[field]: r for r in records}


# ==========================================================================
# 完整性校验
# ==========================================================================

def test_verify_seed_data_passes():
    from store.seed_loader import verify_seed_data

    assert verify_seed_data() == []


def test_verify_catches_broken_data(monkeypatch):
    """把一张收货单挂到不存在的采购单上：verify 必须报出来，load 必须拒绝加载。"""
    from store import seed_loader

    real_read = seed_loader._read_json

    def broken_read(filename: str):
        data = real_read(filename)
        if filename == "receipts.json":
            data = json.loads(json.dumps(data))  # 深拷贝，别污染 lru_cache 里的原件
            data["receipts"][0]["po_no"] = "PO00000000000"
        return data

    monkeypatch.setattr(seed_loader, "_read_json", broken_read)

    errors = seed_loader.verify_seed_data()
    assert any("不存在的采购单" in e for e in errors), errors

    with pytest.raises(RuntimeError):
        seed_loader.load_seed_data()


# ==========================================================================
# 装载
# ==========================================================================

def test_load_counts_and_idempotence():
    from store.seed_loader import load_seed_data
    from store.store_provider import get_fallback_store

    store = get_fallback_store()
    first = load_seed_data(store)
    second = load_seed_data(store)  # 再灌一遍

    # 幂等：同一份种子重复加载只是覆盖同样的键
    assert first == second

    assert {
        "material": 47, "supplier": 12, "warehouse": 4, "stock": 120,
        "quote": 38, "purchase_order": 9, "receipt": 5, "operator": 4,
        "demand": 6, "bom": 8, "contract": 6, "ticket": 5,
        "purchase_policy": 6, "quality_policy": 2,
    }.items() <= first.items()

    # 过渡期：电商 5 键仍在（Phase 2 随旧子图下线时一并删除这两行）
    assert {"employee", "tags_setting", "external_user", "chat_msg", "order"} <= set(first)


def test_stock_written_in_both_namespaces():
    """库存写两份命名空间，两个视角必须都能查到、且是同一份数据。"""
    from store.seed_loader import load_seed_data
    from store.store_provider import get_fallback_store

    store = get_fallback_store()
    load_seed_data(store)

    by_material = store.search(("stock", "M100023"), limit=1000)
    by_wh = store.search(("stock_wh", "WH02"), limit=1000)

    # M100023 在 WH01 / WH03 有库存，WH02 刻意没有（替代料故事依赖这一点）
    assert {i.key for i in by_material} == {"WH01", "WH03"}
    assert "M100031" in {i.key for i in by_wh}

    # 两个视角的总行数一致（各 120 行，含重复的物料-仓库对？没有——一对一行）
    assert len(store.search(("stock",), limit=1000)) == 120
    assert len(store.search(("stock_wh",), limit=1000)) == 120

    # 同一对 (物料, 仓库) 在两个命名空间里逐字段一致
    wh01 = next(i.value for i in by_material if i.key == "WH01")
    wh01_mirror = next(
        i.value for i in store.search(("stock_wh", "WH01"), limit=1000)
        if i.key == "M100023"
    )
    assert wh01 == wh01_mirror


# ==========================================================================
# 剧情锚点：演示故事线依赖的数字必须继续成立
# ==========================================================================

def test_story_substitute_available_in_other_warehouse():
    """断料替代：M100023 缺货 → 替代料 M100031 在 WH02 有现货。"""
    from models.scm_models import StockItem

    materials = _by(_seed("materials.json")["materials"], "material_code")
    assert "M100031" in materials["M100023"]["substitutes"]
    assert "M100023" in materials["M100031"]["substitutes"]  # 互替，图谱双向边

    stock = _by(
        [r for r in _seed("stock.json")["stock"] if r["warehouse_code"] == "WH02"],
        "material_code",
    )
    sub = StockItem(**stock["M100031"])
    assert sub.qty_available == 2600 and not sub.below_safety

    # 原物料在 WH02 没有库存 —— 否则「断料」不成立
    assert "M100023" not in stock


def test_story_quote_spread_at_least_8pct():
    """比价排名：M100023 至少三家报价，最高最低价差 ≥8%。"""
    quotes = [q for q in _seed("quotes.json")["quotes"] if q["material_code"] == "M100023"]
    assert len(quotes) >= 3

    prices = [q["unit_price"] for q in quotes]
    spread = (max(prices) - min(prices)) / min(prices)
    assert spread >= 0.08, f"价差只有 {spread:.2%}，比价场景失去说服力"


def test_story_transfer_between_warehouses():
    """多仓调拨：M400301 在 WH02 低于安全线、WH01 有富余。"""
    from models.scm_models import StockItem

    rows = {
        r["warehouse_code"]: r
        for r in _seed("stock.json")["stock"]
        if r["material_code"] == "M400301"
    }

    short = StockItem(**rows["WH02"])
    rich = StockItem(**rows["WH01"])

    assert short.below_safety and short.qty_available == 100
    assert not rich.below_safety and rich.qty_available == 800


def test_story_purchase_amount_below_large_threshold_with_new_supplier():
    """采购审批：8000×5.60=44800 < 50000（不是大额单），但供应商 S012 是新供应商。"""
    orders = _by(_seed("purchase_orders.json")["purchase_orders"], "po_no")
    suppliers = _by(_seed("suppliers.json")["suppliers"], "supplier_code")

    po = orders["PO20260928001"]
    assert po["qty"] * po["unit_price"] == po["amount"] == 44800.00
    assert po["amount"] < 50000          # 大额规则的阈值，故意压在下面
    assert suppliers[po["supplier_code"]]["is_new"] is True
    assert po["status"] == "in_transit"  # 场景从「在途单收货」接着讲


def test_story_receipt_defect_rate_above_threshold():
    """质检复核：30/920 = 3.26% > 2% 阈值，触发人工复核。"""
    from models.scm_models import ReceiptPlan

    rate = ReceiptPlan.compute_defect_rate(920, 30)
    assert rate == 0.0326
    assert rate > 0.02
    assert f"{rate:.2%}" == "3.26%"     # 审批卡上显示的就是这个字符串


def test_policy_files_have_fallback():
    """策略文件必须各有一条空条件兜底 —— 没有它，某些申请会「无规则命中」。"""
    for filename in ("purchase_policy.json", "quality_policy.json"):
        policies = _seed(filename)["policies"]
        assert any(not p["conditions"] for p in policies), filename
        # priority 降序首命中：优先级必须唯一，否则命中规则由遍历顺序决定
        priorities = [p["priority"] for p in policies]
        assert len(priorities) == len(set(priorities)), filename


# ==========================================================================
# 离线要素提取合成器
# ==========================================================================

def _prompt(schema: dict, *blocks: dict, task: str = "") -> str:
    """复刻线上提示词的骨架：schema 块 + 若干数据块 + 以围栏收尾的任务描述。

    末尾的 `-------------------` 不是装饰：`PromptView.last_block()` 取的是
    倒数第二段，模板少一条收尾围栏，任务输入就会解析成前一个 JSON 块。
    """
    parts = [json.dumps(schema, ensure_ascii=False)]
    parts += [json.dumps(b, ensure_ascii=False) for b in blocks]
    body = "".join(f"\n-------------------\n{p}" for p in parts)
    return (
        f"指令...{body}\n-------------------\n任务描述：\n"
        f"-------------------\n{task}\n-------------------\n\n"
    )


_MATERIALS_BLOCK = {"materials": [
    {"material_code": "M100023", "material_name": "冷轧钢板 1.2mm", "unit": "kg"},
    {"material_code": "M400301", "material_name": "深沟球轴承 6206", "unit": "支"},
]}
_WAREHOUSES_BLOCK = {"warehouses": [
    {"warehouse_code": "WH01", "warehouse_name": "上海临港仓"},
    {"warehouse_code": "WH02", "warehouse_name": "苏州工业园仓"},
]}


def test_purchase_draft_synthesis():
    from llm.offline import registry
    from models.scm_models import PurchaseDraft

    prompt = _prompt(
        PurchaseDraft.model_json_schema(), _MATERIALS_BLOCK, _WAREHOUSES_BLOCK,
        task="给上海临港仓补 8000kg 冷轧钢板，指定供应商 S012，产线备料",
    )
    draft = PurchaseDraft.model_validate_json(registry.build_payload(prompt))

    assert (draft.material_code, draft.material_query) == ("M100023", "冷轧钢板 1.2mm")
    assert (draft.qty, draft.unit) == (8000.0, "kg")
    assert draft.target_warehouse == "WH01"      # 仓库名反查编码
    assert draft.supplier_code == "S012"
    assert "产线备料" in draft.reason             # 提报说明原样保留


def test_purchase_draft_converts_ton_to_kg():
    """吨在抽取层就换算成 kg：下游只见 kg，没人需要记得乘 1000。"""
    from llm.offline import registry
    from models.scm_models import PurchaseDraft

    prompt = _prompt(
        PurchaseDraft.model_json_schema(), _MATERIALS_BLOCK,
        task="M100023 采购 1.5 吨",
    )
    draft = PurchaseDraft.model_validate_json(registry.build_payload(prompt))

    assert (draft.qty, draft.unit) == (1500.0, "kg")


def test_purchase_draft_leaves_unknown_fields_empty():
    """抽不到就不猜：模糊申请留空，由上层走澄清分支。"""
    from llm.offline import registry
    from models.scm_models import PurchaseDraft

    prompt = _prompt(
        PurchaseDraft.model_json_schema(), _MATERIALS_BLOCK, task="随便买点啥",
    )
    draft = PurchaseDraft.model_validate_json(registry.build_payload(prompt))

    assert (draft.material_code, draft.qty, draft.target_warehouse) == ("", 0.0, "")


def test_receipt_draft_synthesis():
    from llm.offline import registry
    from models.scm_models import ReceiptDraft

    po_block = {"po_no": "PO20260928002", "material_code": "M400301",
                "target_warehouse": "WH02", "qty": 1000, "unit": "支"}
    prompt = _prompt(
        ReceiptDraft.model_json_schema(), po_block,
        task="PO20260928002 到货 920 支，抽检发现 30 支磕碰变形",
    )
    draft = ReceiptDraft.model_validate_json(registry.build_payload(prompt))

    assert draft.po_no == "PO20260928002"
    assert (draft.qty_received, draft.qty_defect) == (920, 30)
    assert draft.defect_type == "磕碰变形"
    assert draft.material_code == "M400301"       # 未写明，由 PO 块反查
    assert draft.warehouse_code == "WH02"


def test_receipt_draft_does_not_leak_example_instance():
    """提示词里没有真实 PO 块时，绝不能把 `{example_json}` 的示例物料当结果。

    示例实例恰好也是 M400301@WH02（见 ReceiptDraft.get_example_instance），
    漏进来不但不报错，结果还「看起来对」—— 这种错误只能靠这条用例拦。
    """
    from llm.offline import registry
    from models.scm_models import ReceiptDraft

    prompt = _prompt(
        ReceiptDraft.model_json_schema(),
        task="PO20260928003 到货 100 支，发现 2 支锈蚀",
    )
    draft = ReceiptDraft.model_validate_json(registry.build_payload(prompt))

    assert draft.po_no == "PO20260928003"
    assert draft.material_code == ""
    assert draft.warehouse_code == ""
