"""
供应商管理子图的节点。

链路：欢迎 → 定位物料 → 取报价 + 评分排名 → 出报告。

**只读链路，全程不调大模型**：排名是加权打分算出来的，不是「让模型挑一家」。
比价结论会被用来选供应商、走审批（「为什么选这家」要经得起追问），
每一分都要能还原成「报价/交期/历史表现」三个来源的数字。

评分公式
--------
    score = 0.5 × 价格分 + 0.3 × 准时率 + 0.2 × (1 − 不良率)

价格分 = 最低价 / 本家报价（最低价得 1，其余按比例折减）。三项都归一到 [0,1]，
权重 0.5/0.3/0.2 体现「采购首先看价格，但交期与质量不能忽略」。
TODO(Phase 6)：接入图谱通路的供应商评分（同一公式的第二种数据来源）与
「高于均价 15%」等策略条件；届时两路必须给出同一个排名（测试比对）。

报价从哪来
----------
这里是「子图只读状态」这条约定**唯一**的例外：报价是「按物料拉取」的明细
（38 条覆盖 20 多个物料），全量装进状态没有意义，主图也不为它单开一路装载。
Phase 4 会给它接端口（`get_supplier_port().list_supplier_quotes`，也是 MCP
工具之一），当前的直读与此保持同一个签名形状，换的时候只动 `_quotes` 一处。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List

from langchain_core.runnables import RunnableConfig

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.scm_resolve import resolve_material
from graphs.scm_graph.sub_supplier.sub_supplier_state import (
    SubSupplierState,
    SubSupplierStateField,
)
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    SupplierComparison,
    SupplierScore,
    TaskResult,
)
from observability.progress import emit, node_done
from utils.agent_logger import get_logger

logger = get_logger("sub_supplier_node")

#: 评分权重（价格 / 准时率 / 不良率），三者和为 1
_WEIGHT_PRICE = 0.5
_WEIGHT_ON_TIME = 0.3
_WEIGHT_QUALITY = 0.2


class NodeName(str, Enum):
    WELCOME = "supplier_welcome_node"
    RESOLVE = "supplier_resolve_node"
    REPORT  = "supplier_report_node"


def _query_text(state: SubSupplierState) -> str:
    """这次要比什么；菜单 id 直达时为空（提示补充，不硬编一个物料）。"""
    raw = (state.get(SubSupplierStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.SUPPLIER_QUERY.value:
        return raw
    return ""


def _quotes(material_code: str) -> List[Dict]:
    """按物料取各家报价。

    TODO(Phase 4)：换成 `get_supplier_port().list_supplier_quotes(material_code)`。
    端口尚未建立，先直读 store —— 这是子图里唯一一处查库，原因见模块文档。
    """
    from tools.store_tool import store_client

    return store_client.list_quotes_by_material(material_code)


def welcome_node(state: SubSupplierState, config: RunnableConfig):
    """查询开始前的提示节点。"""
    logger.info("=== 供应商比价 ===")
    emit("supplier_score", detail="准备拉取供应商报价与履约数据")
    return {
        SubSupplierStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME.value,
            execute_result_code=0,
            execute_result_msg="正在汇总供应商报价与履约表现，请稍等。",
            execute_exceptions=[]
        )]
    }


def resolve_node(state: SubSupplierState, config: RunnableConfig):
    """定位要比价的物料（定位不到就追问，不猜）。"""
    logger.info("=== 比价物料定位 ===")

    text = _query_text(state)
    if not text:
        return _failure(
            "请提供要比价的物料，例如「M100023 哪家供应商报价便宜」。",
            "task_input 只给了菜单 id",
        )

    materials = state.get(SubSupplierStateField.MATERIALS) or []
    material = resolve_material(text, materials)
    if material is None:
        return _failure(
            "没有定位到物料，请提供物料编码（形如 M100023）或使用主数据里的物料名称。",
            f"物料主数据里找不到与「{text[:30]}」匹配的物料",
        )

    return {
        SubSupplierStateField.SUPPLIER_QUERY: SupplierComparison(
            material_code=material.material_code,
            material_name=material.material_name,
            unit=material.unit,
            summary="已定位物料，正在取报价",
        ),
        SubSupplierStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.RESOLVE.value,
            execute_result_code=0,
            execute_result_msg=f"定位到物料 {material.material_code}",
            execute_exceptions=[]
        )]
    }


def report_node(state: SubSupplierState, config: RunnableConfig):
    """取报价 → 评分排名 → 出报告。"""
    logger.info("=== 供应商评分排名 ===")

    comparison = state.get(SubSupplierStateField.SUPPLIER_QUERY)
    if comparison is None or not comparison.material_code:  # pragma: no cover - 路由保证
        return _failure("请提供要比价的物料。", "状态里没有定位结果")

    material_code = comparison.material_code
    materials = state.get(SubSupplierStateField.MATERIALS) or []
    material = next((m for m in materials if m.material_code == material_code), None)
    suppliers = {s.supplier_code: s for s in (state.get(SubSupplierStateField.SUPPLIERS) or [])}

    try:
        quotes = _quotes(material_code)
    except Exception as e:
        return _failure(
            f"拉取「{comparison.material_name}」的供应商报价时出错了，请稍后重试或转人工核实。",
            f"报价查询异常：{e}",
        )

    if not quotes:
        return _failure(
            f"「{comparison.material_name}」目前没有供应商报价记录，请转人工核实。",
            f"报价表里没有物料 {material_code}",
        )

    scores = _rank(quotes, suppliers)

    best = scores[0] if scores else None
    spread = ""
    if len(scores) >= 2:
        high, low = scores[0], scores[-1]
        prices = [s.unit_price for s in scores]
        if min(prices) > 0:
            spread = f"，最高与最低报价相差 {max(prices) / min(prices) - 1:.1%}"

    summary = (
        f"共 {len(scores)} 家报价，{best.supplier_name} 综合得分最高"
        f"（{best.score:.3f}，报价 {best.unit_price:.2f} 元）{spread}。"
        if best else "暂无可用报价。"
    )

    report = SupplierComparison(
        material_code=material_code,
        material_name=comparison.material_name or (material.material_name if material else ""),
        unit=material.unit if material else "",
        best_supplier_code=best.supplier_code if best else "",
        scores=scores,
        summary=summary,
    )

    for s in scores:
        emit("supplier_score", detail=(
            f"No.{s.rank} {s.supplier_name}：报价 {s.unit_price:.2f}，"
            f"交期 {s.lead_time_days} 天，得分 {s.score:.3f}"
        ))
    node_done(NodeName.REPORT.value, f"{material_code} best={report.best_supplier_code}")

    return {
        SubSupplierStateField.SUPPLIER_QUERY: report,
        SubSupplierStateField.TASK_RESULT: TaskResult(
            task_result=report.render(),
            task_result_explain=f"按价格/准时率/不良率加权的综合得分排名（{len(scores)} 家）",
            task_result_code=0
        ),
        SubSupplierStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.REPORT.value,
            execute_result_code=0,
            execute_result_msg=f"完成比价排名（{len(scores)} 家供应商）",
            execute_exceptions=[]
        )]
    }


def _rank(quotes: List[Dict], suppliers) -> List[SupplierScore]:
    """评分排名的唯一落点：分数怎么算、凭什么排第一，都在这个函数里。"""
    valid = [q for q in quotes if float(q.get("unit_price") or 0) > 0]
    if not valid:
        return []

    min_price = min(float(q["unit_price"]) for q in valid)

    scores: List[SupplierScore] = []
    for q in valid:
        code = str(q.get("supplier_code") or "")
        info = suppliers.get(code)
        price = float(q["unit_price"])
        price_score = min_price / price  # 最低价得 1，其余按比例折减

        on_time = float(getattr(info, "on_time_rate", 0.0) or 0.0)
        defect = float(getattr(info, "defect_rate", 0.0) or 0.0)

        total = (
            _WEIGHT_PRICE * price_score
            + _WEIGHT_ON_TIME * on_time
            + _WEIGHT_QUALITY * (1.0 - defect)
        )

        scores.append(SupplierScore(
            supplier_code=code,
            supplier_name=getattr(info, "supplier_name", "") or code,
            unit_price=price,
            lead_time_days=int(getattr(info, "lead_time_days", 0) or 0),
            on_time_rate=on_time,
            defect_rate=defect,
            price_score=round(price_score, 4),
            score=round(total, 4),
        ))

    # 得分降序；同分按报价低的在前（价格是采购的第一顺位），再同按编码定序
    scores.sort(key=lambda s: (-s.score, s.unit_price, s.supplier_code))
    for i, s in enumerate(scores, start=1):
        s.rank = i
    return scores


def _failure(reply: str, detail: str) -> Dict:
    logger.warning(f"供应商比价失败：{detail}")
    return {
        SubSupplierStateField.SUPPLIER_QUERY: SupplierComparison(summary=reply),
        SubSupplierStateField.TASK_RESULT: TaskResult(
            task_result=reply, task_result_explain=detail, task_result_code=1
        ),
        SubSupplierStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.REPORT.value,
            execute_result_code=1,
            execute_result_msg="供应商比价未完成",
            execute_exceptions=[detail]
        )]
    }
