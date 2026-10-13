"""
守的是「库存管理 / 供应商管理」两个子图的业务口径（`src/graphs/scm_graph/`）。

  1. **聚合口径**：可用量 = 在库 − 预留、逐仓判安全线、可支撑天数 = 在库 / 日均
     出库 —— 报告里的每个数字都能从 `stock.json` 重新算出来
     （种子里不写死派生值，口径只留一处：`models/scm_models.py::StockItem`）；
  2. **调拨建议双路径一致**：子图按规则算、图谱通道按图投影算，两处是同一份
     规则的两次实现（改其一必改其二），这条用例是它们的对账 —— 从哪调、
     调到哪、调多少逐项相等，且调出仓扣掉调出量后不低于自己的安全线；
  3. **供应商评分与比价同公式**：子图排名与图谱 `compare_suppliers` 名次一致、
     分数对得上；履约评估的价格分是跨物料取平均，可以从 `quotes.json` 重算；
  4. **定位失败就短路**：定位不到目标时轨迹上只留一条 ✗（resolve 节点），
     报告节点根本不该执行 —— 多出来的第二条 ✗ 会让排障误以为报告也失败了。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kg import query as kg_query
from kg.store import get_graph_store

_SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


def _seed(name: str, key: str) -> list:
    with (_SEED / name).open("r", encoding="utf-8") as f:
        return json.load(f)[key]


def _stock_rows(material_code: str) -> dict:
    return {
        row["warehouse_code"]: row
        for row in _seed("stock.json", "stock")
        if row["material_code"] == material_code
    }


def _state(runner, text: str) -> dict:
    result = runner.start(text)
    assert result.error is None, result.error
    return result.state


# ---------------------------------------------------------------- 库存聚合口径

def test_inventory_aggregation_matches_the_seed_contract(runner):
    """分仓明细 + 合计 + 预警 + 可支撑天数，逐项能从种子重算。"""
    report = _state(runner, "M400301 库存还够吗？会不会断料？")["inventory_query"]
    assert report is not None
    seed = _stock_rows("M400301")

    rows = {row.warehouse_code: row for row in report.rows}
    assert set(rows) == set(seed), "分仓明细要覆盖该物料的每个仓"
    for code, row in rows.items():
        source = seed[code]
        assert row.qty_available == source["on_hand"] - source["reserved"], (
            "可用量 = 在库 − 预留"
        )
        assert row.below_safety is (row.qty_available < row.safety_stock), (
            "安全线是逐仓判定，且按可用量判"
        )

    # 明细按可用量降序：人看库存第一眼想知道「货在哪」
    assert [r.qty_available for r in report.rows] == sorted(
        (r.qty_available for r in report.rows), reverse=True
    )
    assert report.total_available == sum(r.qty_available for r in report.rows)
    assert report.total_on_hand == sum(r.on_hand for r in report.rows)

    on_hand = sum(r["on_hand"] for r in seed.values())
    out_per_day = sum(r["avg_daily_out"] for r in seed.values())
    assert report.turnover_days == pytest.approx(round(on_hand / out_per_day, 1))

    assert report.below_safety is True
    assert "WH02" in report.summary, "低于安全线的仓要点名"


def test_transfer_suggestion_agrees_with_the_graph_channel(runner):
    """调拨建议的双路径对账：子图（规则）与图谱（图投影）同一条建议。"""
    report = _state(runner, "M400301 库存还够吗？会不会断料？")["inventory_query"]
    assert len(report.transfers) == 1
    transfer = report.transfers[0]

    facts = kg_query.stock_distribution(get_graph_store(), "M400301")
    graph_transfer = next((f for f in facts if f.key.endswith("|transfer")), None)
    assert graph_transfer is not None, "图谱通道也要给出这条建议（两处规则照抄）"

    text = graph_transfer.text
    assert f"从 {transfer.from_warehouse} 调拨 {transfer.qty}" in text
    assert f"到 {transfer.to_warehouse}" in text

    # 规则本身：缺口仓 = 缺口最大的仓；余量仓 = 余量最大的仓；量 = min(缺口, 余量)
    seed = _stock_rows("M400301")
    short = seed[transfer.to_warehouse]
    rich = seed[transfer.from_warehouse]
    gap = short["safety_stock"] - (short["on_hand"] - short["reserved"])
    surplus = (rich["on_hand"] - rich["reserved"]) - rich["safety_stock"]
    assert transfer.qty == min(gap, surplus)
    # 调出后调出仓仍不低于自己的安全线 —— 调拨不能拆东墙补西墙
    assert (rich["on_hand"] - rich["reserved"]) - transfer.qty >= rich["safety_stock"]


def test_no_transfer_advice_when_every_warehouse_is_short(runner):
    """全仓都低于安全线：只报预警并建议补货，不许硬凑一条调拨。"""
    report = _state(runner, "M100023 各个仓库还有多少库存？")["inventory_query"]

    assert report.below_safety is True
    assert report.transfers == [], "没有余量仓，调拨建议就不该存在"
    assert "补货" in report.summary, "该给的是一条能做事的建议，而不是一句空结论"

    facts = kg_query.stock_distribution(get_graph_store(), "M100023")
    assert not any(f.key.endswith("|transfer") for f in facts)


# ---------------------------------------------------------------- 供应商评分

def test_supplier_comparison_matches_the_graph_channel(runner):
    """比价双路径对账：同一批报价、同一个公式，名次与分数必须一致。"""
    report = _state(runner, "M100023 哪家供应商报价便宜？")["supplier_query"]
    assert report.best_supplier_code == "S007"

    facts = kg_query.compare_suppliers(get_graph_store(), "M100023")
    details = {f.key.rsplit("|", 1)[-1]: f.text for f in facts[1:]}
    assert {s.supplier_code for s in report.scores} == set(details)

    assert [s.supplier_code for s in report.scores] == [
        f.key.rsplit("|", 1)[-1] for f in facts[1:]
    ], "名次也要一致（两处同一条排序规则）"
    for score in report.scores:
        assert f"{score.score:.2f}" in details[score.supplier_code]
        assert f"{score.unit_price:g}" in details[score.supplier_code]


def test_supplier_evaluation_is_deterministic_and_recomputable(runner):
    """履约评估：连查两次逐字段相等；价格分跨物料取平均，可从报价种子重算。"""
    question = "帮我看看 S007 这家供应商的履约情况，交期和不良率怎么样"
    first = _state(runner, question)["supplier_query"]
    second = _state(runner, question)["supplier_query"]

    assert first.model_dump() == second.model_dump(), "只读计算必须是确定性的"

    quotes = _seed("quotes.json", "quotes")
    mine = [q for q in quotes if q["supplier_code"] == "S007"]
    lowest = {}
    for q in quotes:
        code = q["material_code"]
        lowest[code] = min(lowest.get(code, q["unit_price"]), q["unit_price"])

    assert first.quoted_count == len(mine)
    assert first.lowest_count == sum(1 for q in mine if q["unit_price"] <= lowest[q["material_code"]])
    expected_avg = round(
        sum(lowest[q["material_code"]] / q["unit_price"] for q in mine) / len(mine), 4
    )
    assert first.avg_price_score == pytest.approx(expected_avg, abs=1e-4)

    # 综合评分与比价共用一套 0.5/0.3/0.2
    expected_score = round(
        0.5 * first.avg_price_score
        + 0.3 * first.on_time_rate
        + 0.2 * (1 - first.defect_rate),
        4,
    )
    assert first.score == pytest.approx(expected_score, abs=1e-4)


def test_supplier_resolve_failure_short_circuits_the_report(runner):
    """定位不到目标：只留一条 ✗，报告节点不执行（失败不该走两遍）。"""
    result = runner.start("帮我看下 S999 这家供应商的履约情况")

    assert result.error is None
    assert result.task_result.task_result_code == 1, "没定位到目标是等补充输入"

    failed = [
        nr.execute_node_name
        for nr in result.state["node_result"]
        if nr.execute_result_code != 0
    ]
    assert failed == ["supplier_resolve_node"], (
        "失败信号只能由 resolve 节点给出（report 没执行，更不该再 ✗ 一次）"
    )
    assert not any(
        "supplier_report_node" in chunk for chunk in result.updates if isinstance(chunk, dict)
    ), "定位失败就该短路到 END，报告节点不在执行轨迹里"

    assert "S999" in result.task_result.task_result or "编码" in result.task_result.task_result


def test_supplier_evaluation_falls_back_when_graph_is_unavailable(runner, monkeypatch):
    """图谱不可用：履约评估退化为基础指标并如实标注，不猜价格竞争力。"""
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "graph")

    from integrations.factory import reset_ports
    from kg.store import reset_graph_store

    reset_ports()
    reset_graph_store()
    try:
        report = _state(runner, "S007 的履约情况怎么样？")["supplier_query"]
    finally:
        monkeypatch.delenv("MOCK_FAIL_ACTIONS", raising=False)
        reset_ports()
        reset_graph_store()

    assert report.quoted_count == 0
    assert report.note, "降级必须如实标注"
    assert report.lead_time_days > 0 and report.on_time_rate > 0, "基础指标照常给出"
