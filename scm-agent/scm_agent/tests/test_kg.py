"""
守的是知识图谱层的四条线（`src/kg/`）。

  1. **投影**：图是从种子 JSON 投影出来的同一份事实（不是第二份真相）——
     节点/边计数与 JSON 逐类对得上，重复投影幂等，库存口径由模型计算后进图；
  2. **查询**：三个多跳查询的剧情锚点数字（替代料 / 比价 / 调拨）—— 这些数字
     就是种子里的数字，改了种子这条用例先亮；
  3. **链接与分派**：编码直命中优先；定位不到就不参与（不猜具体物料）；
     一句话命中多类关系时多路都跑；
  4. **降级与红线**：故障注入下 `retrieve()` 返回空并把级别标到 L1，
     **永不抛出**；neo4j 不可用回落内存且有 warning；kg 不许 import
     rag / graphs / webapp（子进程里验，进程内 import 过的话函数内 import
     会骗过断言）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kg import query as kg_query
from kg.link import link_material, link_supplier, link_warehouse
from kg.memory_store import MemoryGraphStore
from kg.schema import node_id, NodeKind, stock_item_key
from kg.store import get_graph_store, reset_graph_store
from observability import degrade as degrade_mod
from store.seed_loader import build_knowledge_graph

_SEED = Path(__file__).resolve().parents[1] / "data" / "seed"
_SRC = Path(__file__).resolve().parents[1] / "src"


def _seed(name: str, key: str) -> list:
    with (_SEED / name).open("r", encoding="utf-8") as f:
        return json.load(f)[key]


# ---------------------------------------------------------------- 投影

def test_projection_counts_match_seed_records():
    """节点/边计数与 JSON 逐类对得上 —— 图多一条或少一条都说明投影规则漂了。"""
    store = get_graph_store()
    stats = store.stats()

    materials = _seed("materials.json", "materials")
    suppliers = _seed("suppliers.json", "suppliers")
    warehouses = _seed("warehouses.json", "warehouses")
    stock = _seed("stock.json", "stock")
    quotes = _seed("quotes.json", "quotes")
    orders = _seed("purchase_orders.json", "purchase_orders")
    receipts = _seed("receipts.json", "receipts")
    policies = (_seed("purchase_policy.json", "policies")
                + _seed("quality_policy.json", "policies"))

    expected_nodes = {
        "material": len(materials),
        "supplier": len(suppliers),
        "warehouse": len(warehouses),
        "stock_item": len(stock),
        "po": len(orders),
        "receipt": len(receipts),
        "policy": len(policies),
    }
    expected_edges = {
        "SUBSTITUTES": sum(len(m.get("substitutes") or []) for m in materials),
        "SUPPLIES": len(quotes),
        # 库存快照 / 采购单 / 收货单各挂一条 IS_MATERIAL
        "IS_MATERIAL": len(stock) + len(orders) + len(receipts),
        "STOCKED_AT": len(stock),
        "SOURCED_FROM": len(orders),
        "RECEIVED_AT": len(receipts),
    }

    assert stats["by_kind"] == expected_nodes
    assert stats["by_edge"] == expected_edges
    assert stats["node"] == sum(expected_nodes.values())
    assert stats["edge"] == sum(expected_edges.values())


def test_projection_is_idempotent():
    """重复投影只是覆盖同样的键：计数不涨，节点不重复。"""
    graph = MemoryGraphStore()
    first = build_knowledge_graph(graph)
    second = build_knowledge_graph(graph)

    assert first["node"] == second["node"] and first["edge"] == second["edge"]
    stats = graph.stats()
    assert stats["node"] == first["node"], "重复投影把节点写重了"
    assert stats["edge"] == first["edge"], "重复投影把边写重了"


def test_stock_node_props_come_from_the_model_formula():
    """库存口径（可用量/安全线/可支撑天数）由 StockItem 计算后进图 —— 与库存子图同源。"""
    store = get_graph_store()
    node = store.get_node(node_id(NodeKind.STOCK_ITEM, stock_item_key("M100023", "WH01")))
    assert node is not None

    assert node.props["qty_available"] == node.props["on_hand"] - node.props["reserved"]
    assert node.props["below_safety"] is True     # 700 < 1000
    assert node.props["turnover_days"] == round(900 / 60, 1)


# ---------------------------------------------------------------- 三个多跳查询

def test_substitute_chain_matches_story_line():
    """替代料：物料 → 替代料 → 有货仓 → 报价最低的供应商（三跳链）。"""
    facts = kg_query.find_substitutes(get_graph_store(), "M100023")

    assert len(facts) == 1
    fact = facts[0]
    assert fact.raw == 6.0 and fact.hops == 3
    # 数字全部来自节点属性：WH02 可用 2600（三仓合计 5200）、S007 报价 4.85 / 2 天
    assert "M100031" in fact.text and "冷轧钢板 1.5mm" in fact.text
    assert "2600" in fact.text and "5200" in fact.text
    assert "4.85" in fact.text and "2 天" in fact.text
    assert fact.source.startswith("图谱路径")


def test_substitute_absent_is_an_honest_negative_fact():
    """没登记替代料就如实说「没有」—— 否定性事实也是事实，不能查不到就静默。"""
    facts = kg_query.find_substitutes(get_graph_store(), "M100011")
    assert len(facts) == 1
    assert facts[0].raw == 2.0
    assert "没有登记替代物料" in facts[0].text


def test_compare_suppliers_ranking_and_spread():
    """比价：一条汇总 + 各家明细，评分与 sub_supplier 同公式（0.5/0.3/0.2）。"""
    facts = kg_query.compare_suppliers(get_graph_store(), "M100023")

    summary = facts[0]
    assert summary.raw == 5.0
    assert "3 家" in summary.text
    assert "5.12" in summary.text and "5.8" in summary.text and "13.3%" in summary.text
    assert "宁波振兴钢贸" in summary.text and "0.97" in summary.text

    detail = {f.key.rsplit("|", 1)[-1]: f for f in facts[1:]}
    assert set(detail) == {"S001", "S007", "S012"}   # 三家报价都在
    assert "0.93" in detail["S001"].text
    assert "0.66" in detail["S012"].text


def test_stock_distribution_and_transfer_advice():
    """库存分布 + 调拨建议：从余量仓调往缺口仓，调完不低于调出仓的安全线。"""
    facts = kg_query.stock_distribution(get_graph_store(), "M400301")

    dist = next(f for f in facts if f.key.endswith("|distribution"))
    assert "WH01 仓可用 800 支" in dist.text
    assert "WH02 仓可用 100 支（低于安全库存）" in dist.text

    transfer = next(f for f in facts if f.key.endswith("|transfer"))
    assert transfer.raw == 5.5
    assert "WH01" in transfer.text and "WH02" in transfer.text
    assert "100 支" in transfer.text
    # 调出仓（WH01 余量 800-300=500）扣掉调出量后仍不低于安全线
    assert "700 支" in transfer.text and "300 支" in transfer.text


def test_no_transfer_advice_when_every_warehouse_is_short():
    """全仓低于安全线时给不出调拨建议 —— 没有余量仓可调，不许硬凑一条。"""
    facts = kg_query.stock_distribution(get_graph_store(), "M100023")
    assert facts, "库存分布本身还是要报"
    assert not any(f.key.endswith("|transfer") for f in facts)


# ---------------------------------------------------------------- 链接与分派

def test_linking_prefers_codes_and_refuses_to_guess():
    """编码最硬；名称可认；泛问与图上没有的编码一律不猜。"""
    store = get_graph_store()

    assert link_material("M100023 断料了", store) == "M100023"
    assert link_material("m100023", store) == "M100023"
    assert link_material("冷轧钢板 1.2mm 断料了", store) == "M100023"
    # 泛问（多个物料共享「冷轧钢板」这个核心名）→ 分辨不出，弃权
    assert link_material("冷轧钢板还有多少", store) is None
    # 编码写出来了但图上没有 → 不落到别的物料上
    assert link_material("M123456 还有库存吗", store) is None

    assert link_supplier("S007 交期怎么样", store) == "S007"
    assert link_warehouse("WH02 还剩多少", store) == "WH02"


def test_retrieve_dispatches_relation_kinds_and_skips_doc_questions():
    """一句话命中多类关系时多路都跑；文档类问题根本不进图谱。"""
    chunks = kg_query.retrieve("M100023 断料了，有没有可以替代的物料？有货吗？")

    kinds = {c.key.split("|")[1] for c in chunks}
    assert {"substitute", "stock"} <= kinds
    assert all(c.kind == "graph" for c in chunks)
    assert all(c.doc_title == kg_query.GRAPH_DOC_TITLE for c in chunks)
    assert len(chunks) <= kg_query.GRAPH_TAKE
    assert chunks[0].raw == max(c.raw for c in chunks), "图事实按预设分降序"

    # 没有关系词：图谱通道不参与（文档通道的事）
    assert kg_query.retrieve("采购申请的审批流程是什么？") == []


# ---------------------------------------------------------------- 降级与红线

def test_failure_injection_marks_l1_and_never_raises(monkeypatch):
    """`MOCK_FAIL_ACTIONS=graph`：返回空 + L1，不把异常抛给检索链路。"""
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "graph")
    degrade_mod.reset()

    assert kg_query.retrieve("M100023 断料了，有替代料吗？") == []
    assert degrade_mod.current() == degrade_mod.DegradeLevel.L1
    assert degrade_mod.reasons(), "降级要说明原因"

    # 撤掉注入立刻恢复（同一进程内）
    monkeypatch.delenv("MOCK_FAIL_ACTIONS")
    degrade_mod.reset()
    assert kg_query.retrieve("M100023 断料了，有替代料吗？")


def test_neo4j_backend_falls_back_to_memory_with_warning(monkeypatch, caplog):
    """neo4j 配了但连不上：打 warning、回落内存 —— 不是错误路径，是设计内行为。"""
    # 指向本机一个必然拒绝的端口：无论装没装驱动、`.env` 里有没有配 URI，
    # 这一条都走「连不上 -> 回落」的同一个分支，不依赖环境
    monkeypatch.setenv("GRAPH_STORE", "neo4j")
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:9")

    reset_graph_store()
    with caplog.at_level(logging.WARNING):
        store = get_graph_store()

    assert isinstance(store, MemoryGraphStore)
    assert any("回退内存图存储" in r.message for r in caplog.records)
    assert store.stats()["node"] > 0, "回落之后图要照样建出来"


def test_kg_layer_does_not_import_upper_layers():
    """分层红线：kg 不 import rag / graphs / webapp。

    在**子进程**里验：本测试进程跑过 rag 的用例，`sys.modules` 里早就有 rag 了，
    进程内断言等于永远失败（或者反过来，被函数内 import 骗过）。
    """
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import kg.query;"
        "bad = sorted(m for m in ('rag', 'graphs', 'webapp')"
        " if any(k == m or k.startswith(m + '.') for k in sys.modules));"
        "print(','.join(bad))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(_SRC)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"kg 引入了上层模块：{proc.stdout.strip()}"
