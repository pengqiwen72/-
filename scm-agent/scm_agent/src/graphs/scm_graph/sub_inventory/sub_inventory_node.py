"""
库存管理子图的节点。

链路：欢迎 → 定位物料 + 聚合 → 出报告。

**只读链路，全程不调大模型**：定位用确定性规则（`scm_resolve`），聚合是加减乘除。
数字类问题最怕「差不多」——模型改写一个数量、或者把「可用量」说成「在库量」，
用户按它去下单就会缺料。所以这条链路里模型没有位置，这也是与问答链路的分工：
**制度类问题走 RAG，数字类问题走确定性计算**。

聚合口径（与 `models.StockItem` 的属性定义同一处口径）
------------------------------------------------------
  * 可用量 = 在库 − 预留（预警看可用量，不看在库量）
  * 低于安全线 = 可用量 < 安全库存线（逐仓判定，全仓一处低于就算）
  * 可支撑天数 = 全仓在库 / 全仓近 30 天日均出库

调拨建议（规则与图谱通道同源）
------------------------------
本链路用状态里的库存快照算，`kg/query.py::stock_distribution` 用图投影算 ——
同一批种子数据的两种来源，**必须给出同一个答案**（测试逐项比对）。所以规则
只写一遍在这里、照抄一遍在那里，改其一必改其二：

  * 缺口仓 = 低于安全线的仓里**缺口最大**的（缺口 = 安全线 − 可用量）；
  * 余量仓 = 不低于安全线的仓里**余量最大**的（余量 = 可用量 − 安全线）；
  * 调拨量 = min(缺口, 余量)：以补齐缺口为准，且调出仓调出后仍不得低于
    自己的安全线。

两者缺一就不提建议：全仓都缺（没有余量仓）说明要靠补货而不是调拨；没有
缺口仓时更不该多事。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List

from langchain_core.runnables import RunnableConfig

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.scm_resolve import resolve_material
from graphs.scm_graph.sub_inventory.sub_inventory_state import (
    SubInventoryState,
    SubInventoryStateField,
)
from models.scm_models import (
    InventoryReport,
    NodeResult,
    StockItem,
    TaskResult,
    TransferSuggestion,
)
from observability.progress import emit, node_done
from utils.agent_logger import get_logger

logger = get_logger("sub_inventory_node")


class NodeName(str, Enum):
    WELCOME = "inventory_welcome_node"
    QUERY   = "inventory_query_node"


def _query_text(state: SubInventoryState) -> str:
    """这次要查什么；菜单 id 直达时为空（提示补充，不硬编一个物料）。"""
    raw = (state.get(SubInventoryStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.INVENTORY_QUERY.value:
        return raw
    return ""


def welcome_node(state: SubInventoryState, config: RunnableConfig):
    """查询开始前的提示节点。"""
    logger.info("=== 库存查询 ===")
    emit("stock_aggregate", detail="准备汇总多仓库存快照")
    return {
        SubInventoryStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME.value,
            execute_result_code=0,
            execute_result_msg="正在汇总各仓库存与安全库存，请稍等。",
            execute_exceptions=[]
        )]
    }


def query_node(state: SubInventoryState, config: RunnableConfig):
    """定位物料 → 聚合分仓明细 → 出报告。"""
    logger.info("=== 库存聚合 ===")

    text = _query_text(state)
    if not text:
        return _failure(
            "请提供要查询的物料，例如「M100023 还有多少库存」或「冷轧钢板各仓库存」。",
            "task_input 只给了菜单 id",
        )

    materials = state.get(SubInventoryStateField.MATERIALS) or []
    material = resolve_material(text, materials)
    if material is None:
        return _failure(
            "没有定位到物料，请提供物料编码（形如 M100023）或使用主数据里的物料名称。",
            f"物料主数据里找不到与「{text[:30]}」匹配的物料",
        )

    stock_items = state.get(SubInventoryStateField.STOCK_ITEMS) or []
    rows = [s for s in stock_items if s.material_code == material.material_code]
    if not rows:
        return _failure(
            f"没有查到「{material.material_name}」的库存记录，可能该物料未在任一仓库建档。",
            f"库存快照里没有物料 {material.material_code}",
        )

    report = _aggregate(material, rows)

    transfer_note = ""
    if report.transfers:
        t = report.transfers[0]
        transfer_note = (
            f"；建议从 {t.from_warehouse} 调拨 {t.qty}{report.unit} 到 {t.to_warehouse}"
        )

    emit(
        "stock_aggregate",
        detail=(
            f"{len(rows)} 个仓库，可用合计 {report.total_available}{report.unit}"
            + ("（存在低于安全线的仓）" if report.below_safety else "")
            + transfer_note
        ),
    )
    node_done(NodeName.QUERY.value,
              f"{material.material_code} available={report.total_available} below={report.below_safety}")

    return {
        SubInventoryStateField.INVENTORY_QUERY: report,
        SubInventoryStateField.TASK_RESULT: TaskResult(
            task_result=report.render(),
            task_result_explain=f"按 {len(rows)} 个仓库的库存快照聚合",
            task_result_code=0
        ),
        SubInventoryStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.QUERY.value,
            execute_result_code=0,
            execute_result_msg=f"完成库存聚合（{material.material_code}，{len(rows)} 个仓库）",
            execute_exceptions=[]
        )]
    }


def _aggregate(material, rows: List[StockItem]) -> InventoryReport:
    """聚合口径唯一落点：明细排序、合计、预警、一句话结论都在这里算。"""
    # 按可用量降序排：人看库存第一眼想知道「货在哪」，而不是按仓库编码排
    rows = sorted(rows, key=lambda s: s.qty_available, reverse=True)

    total_on_hand = sum(s.on_hand for s in rows)
    total_available = sum(s.qty_available for s in rows)
    safety = sum(s.safety_stock for s in rows)
    out_per_day = sum(s.avg_daily_out for s in rows)
    turnover = round(total_on_hand / out_per_day, 1) if out_per_day > 0 else 0.0

    below = [s for s in rows if s.below_safety]
    transfers = _suggest_transfer(rows, material.unit)

    if below:
        names = "、".join(s.warehouse_code for s in below)
        if transfers:
            t = transfers[0]
            advice = (
                f"，建议从 {t.from_warehouse} 调拨 {t.qty}{material.unit}"
                f"至 {t.to_warehouse}"
            )
        else:
            advice = "，全仓没有余量可调，建议尽快补货"
        summary = (
            f"全仓可用 {total_available}{material.unit}，"
            f"{names} 低于安全库存线{advice}。"
        )
    else:
        summary = f"全仓可用 {total_available}{material.unit}，各仓均在安全线以上。"

    if turnover:
        summary += f"，按近 30 天出库可支撑 {turnover} 天"

    return InventoryReport(
        material_code=material.material_code,
        material_name=material.material_name,
        unit=material.unit,
        total_on_hand=total_on_hand,
        total_available=total_available,
        safety_stock=safety,
        below_safety=bool(below),
        turnover_days=turnover,
        rows=rows,
        transfers=transfers,
        summary=summary,
    )


def _suggest_transfer(rows: List[StockItem], unit: str) -> List[TransferSuggestion]:
    """跨仓调拨建议：余量仓 → 缺口仓。

    规则见模块文档。挑两个「最」而不是贪心配平多对：演示场景里缺口的戏码
    集中在某一个仓，挑最急的缺口、找余量最大的仓，一条建议讲清楚就够；
    多对配对是补货计划的事，不属于查询链路。
    """
    short_rows = [s for s in rows if s.below_safety]
    rich_rows = [s for s in rows if not s.below_safety]
    if not short_rows or not rich_rows:
        return []

    short = max(short_rows, key=lambda s: s.safety_stock - s.qty_available)
    rich = max(rich_rows, key=lambda s: s.qty_available - s.safety_stock)
    gap = short.safety_stock - short.qty_available
    surplus = rich.qty_available - rich.safety_stock
    qty = min(gap, surplus)
    if qty <= 0:  # pragma: no cover - 两个分支的构造保证 qty > 0
        return []

    return [TransferSuggestion(
        material_code=short.material_code,
        from_warehouse=rich.warehouse_code,
        to_warehouse=short.warehouse_code,
        qty=qty,
        reason=(
            f"{short.warehouse_code} 低于安全线"
            f"（可用 {short.qty_available}{unit} < 安全线 {short.safety_stock}{unit}），"
            + ("按缺口量调拨" if qty == gap else "先按余量上限调拨")
            + f"；调出后 {rich.warehouse_code} 可用 {rich.qty_available - qty}{unit}，"
            f"仍不低于其安全线 {rich.safety_stock}{unit}"
        ),
    )]


def _failure(reply: str, detail: str) -> Dict:
    logger.warning(f"库存查询失败：{detail}")
    return {
        SubInventoryStateField.INVENTORY_QUERY: InventoryReport(
            summary=reply, below_safety=False
        ),
        SubInventoryStateField.TASK_RESULT: TaskResult(
            task_result=reply, task_result_explain=detail, task_result_code=1
        ),
        SubInventoryStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.QUERY.value,
            execute_result_code=1,
            execute_result_msg="库存查询未完成",
            execute_exceptions=[detail]
        )]
    }
