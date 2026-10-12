"""
供应商管理子图的节点。

链路：欢迎 → 定位（物料或供应商）→ 出报告。

定位到**物料**出比价排名，定位到**供应商**出履约评估 —— 两种报告，一个入口。
目标的优先级是物料在前：一句话里同时带了 M 码与 S 码时（「S007 给 M100023
的报价贵不贵」），比价报告本来就覆盖了指定供应商的报价，不会丢信息。

**只读链路，全程不调大模型**：排名是加权打分算出来的，不是「让模型挑一家」。
比价结论会被用来选供应商、走审批（「为什么选这家」要经得起追问），
每一分都要能还原成「报价/交期/历史表现」三个来源的数字。

评分公式（比价与履约评估共用同一套权重）
----------------------------------------
    score = 0.5 × 价格分 + 0.3 × 准时率 + 0.2 × (1 − 不良率)

价格分 = 最低价 / 本家报价（最低价得 1，其余按比例折减）。三项都归一到 [0,1]，
权重 0.5/0.3/0.2 体现「采购首先看价格，但交期与质量不能忽略」。图谱通道的
比价查询（`kg/query.py::compare_suppliers`）逐字照抄了同一公式，两路排名
必须相同（测试比对）；评估模式下价格分是**跨该供应商在报的各物料**取平均，
聚合语义与比价一致。

报价从哪来（两种目标、两个来源）
--------------------------------
* **比价**（按物料）：报价是「按物料拉取」的明细（38 条覆盖 20 多个物料），
  全量装进状态没有意义，主图也不为它单开一路装载。所以它走端口
  `get_supplier_port().list_supplier_quotes`（也是 MCP 工具之一）：
  `TOOL_GATEWAY=mcp` 时这条报价链路同样穿过网关。归一是显式一步，
  `_rank` 只认内部口径（unit_price 元），不认上游的 quotPrice 分。
* **履约评估**（按供应商）：问题变成「这家都供哪些料」，是 SUPPLIES 边上的
  反向遍历 —— 端口没有（也不该为一次查询新增）按供应商查报价的方法，
  这类多跳问题走图谱通道 `get_graph_store()`，与 RAG 的关系类问题同源。
  取数前先过 `kg/query.py::guard()`（熔断器 + `MOCK_FAIL_ACTIONS=graph` 的
  唯一判定点），图谱不可用时降级：只给基础履约指标（交期/准时率/不良率），
  价格竞争力一项如实标「未计算」，不猜。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List

from langchain_core.runnables import RunnableConfig

from graphs.scm_graph.scm_node import IntentDetection
from graphs.scm_graph.scm_resolve import resolve_material, resolve_supplier
from graphs.scm_graph.sub_supplier.sub_supplier_state import (
    SubSupplierState,
    SubSupplierStateField,
)
from integrations.factory import get_supplier_port
from integrations.normalize import normalize_quote
from kg.query import guard
from kg.schema import EdgeKind, NodeKind, node_id
from kg.store import get_graph_store
from models.scm_models import (
    MaterialInfo,
    NodeResult,
    SupplierComparison,
    SupplierEvaluation,
    SupplierPriceLine,
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
    """按物料取各家报价（经端口 + 归一到内部口径）。

    归一是显式的一步：上游（SRM 报文 quotPrice 分）在这里变成内部口径
    （unit_price 元），与 store 时代 `list_quotes_by_material` 的字段形状
    完全一致 —— 所以 `_rank` 一行没动。
    """
    return [
        normalize_quote(q)
        for q in get_supplier_port().list_supplier_quotes(material_code)
    ]


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
    """定位比价物料或评估对象供应商（定位不到就追问，不猜）。"""
    logger.info("=== 供应商链路目标定位 ===")

    text = _query_text(state)
    if not text:
        return _failure(
            "请提供要比价的物料（如「M100023 哪家供应商报价便宜」）"
            "或要评估的供应商（如「S007 的履约情况怎么样」）。",
            "task_input 只给了菜单 id",
            node=NodeName.RESOLVE,
        )

    # 物料优先：同时带 M 码与 S 码时回答比价（覆盖全量报价，信息更多）
    materials = state.get(SubSupplierStateField.MATERIALS) or []
    material = resolve_material(text, materials)
    if material is not None:
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
                execute_result_msg=f"定位到物料 {material.material_code}（比价）",
                execute_exceptions=[]
            )]
        }

    suppliers = state.get(SubSupplierStateField.SUPPLIERS) or []
    supplier = resolve_supplier(text, suppliers)
    if supplier is not None:
        return {
            SubSupplierStateField.SUPPLIER_QUERY: SupplierEvaluation(
                supplier_code=supplier.supplier_code,
                supplier_name=supplier.supplier_name,
                summary="已定位供应商，正在汇总履约数据",
            ),
            SubSupplierStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.RESOLVE.value,
                execute_result_code=0,
                execute_result_msg=f"定位到供应商 {supplier.supplier_code}（履约评估）",
                execute_exceptions=[]
            )]
        }

    return _failure(
        "没有定位到物料或供应商，请提供编码（形如 M100023 / S007）或使用主数据里的名称。",
        f"主数据里找不到与「{text[:30]}」匹配的物料或供应商",
        node=NodeName.RESOLVE,
    )


def report_node(state: SubSupplierState, config: RunnableConfig):
    """按定位结果分派：物料 → 取报价排名；供应商 → 履约评估。"""
    target = state.get(SubSupplierStateField.SUPPLIER_QUERY)
    if isinstance(target, SupplierEvaluation):
        return _report_evaluation(state, target)
    if target is None or not target.material_code:  # pragma: no cover - 路由保证
        return _failure("请提供要比价的物料。", "状态里没有定位结果")
    return _report_comparison(state, target)


def _report_comparison(state: SubSupplierState, comparison: SupplierComparison):
    """取报价 → 评分排名 → 出报告。"""
    logger.info("=== 供应商评分排名 ===")

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


def _report_evaluation(state: SubSupplierState, target: SupplierEvaluation):
    """履约评估：基础履约指标（状态） + 价格竞争力（图谱） → 综合评分。"""
    logger.info("=== 供应商履约评估 ===")

    suppliers = {
        s.supplier_code: s
        for s in (state.get(SubSupplierStateField.SUPPLIERS) or [])
    }
    info = suppliers.get(target.supplier_code)
    if info is None:  # pragma: no cover - 定位已经确认过存在
        return _failure(
            f"主数据里没有供应商 {target.supplier_code} 的记录。",
            f"供应商主数据里找不到 {target.supplier_code}",
        )

    lines: List[SupplierPriceLine] = []
    note = ""
    try:
        lines = _quoted_lines(target.supplier_code)
    except Exception as e:  # 图谱不可用是设计内降级（缺驱动/熔断/故障注入）
        note = "图谱通道本次不可用，价格竞争力未能计算，仅给出基础履约指标。"
        logger.warning(f"履约评估取报价失败：{e}")

    quoted = len(lines)
    lowest = sum(1 for line in lines if line.is_lowest)
    avg_price_score = round(sum(line.price_score for line in lines) / quoted, 4) if quoted else 0.0
    score = round(
        _WEIGHT_PRICE * avg_price_score
        + _WEIGHT_ON_TIME * info.on_time_rate
        + _WEIGHT_QUALITY * (1.0 - info.defect_rate),
        4,
    ) if quoted else 0.0

    if info.is_new:
        note = (note + " " if note else "") + "新供应商暂无履约记录，准时率与不良率按 0 计入评分。"

    if quoted:
        summary = (
            f"承诺交期 {info.lead_time_days} 天，准时交付率 {info.on_time_rate:.0%}，"
            f"到货不良率 {info.defect_rate:.2%}；在报 {quoted} 个物料中 "
            f"{lowest} 个为最低报价，综合履约评分 {score:.3f}。"
        )
    else:
        summary = (
            f"承诺交期 {info.lead_time_days} 天，准时交付率 {info.on_time_rate:.0%}，"
            f"到货不良率 {info.defect_rate:.2%}。"
        )

    report = SupplierEvaluation(
        supplier_code=info.supplier_code,
        supplier_name=info.supplier_name,
        is_new=bool(info.is_new),
        cooperation_since=info.cooperation_since or "",
        lead_time_days=int(info.lead_time_days or 0),
        on_time_rate=float(info.on_time_rate or 0.0),
        defect_rate=float(info.defect_rate or 0.0),
        quoted_count=quoted,
        lowest_count=lowest,
        avg_price_score=avg_price_score,
        score=score,
        lines=lines,
        note=note,
        summary=summary,
    )

    emit("supplier_score", detail=(
        f"{info.supplier_code} {info.supplier_name}：交期 {report.lead_time_days} 天，"
        f"准时率 {report.on_time_rate:.0%}，不良率 {report.defect_rate:.2%}"
        + (f"，综合履约评分 {report.score:.3f}（{quoted} 个在报物料）" if quoted else "")
    ))
    node_done(NodeName.REPORT.value,
              f"{info.supplier_code} score={report.score} lines={quoted}")

    return {
        SubSupplierStateField.SUPPLIER_QUERY: report,
        SubSupplierStateField.TASK_RESULT: TaskResult(
            task_result=report.render(),
            task_result_explain=(
                f"同一套 0.5/0.3/0.2 加权口径；价格分跨 {quoted} 个在报物料取平均"
                if quoted else "图谱不可用，仅基础履约指标"
            ),
            task_result_code=0
        ),
        SubSupplierStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.REPORT.value,
            execute_result_code=0,
            execute_result_msg=f"完成履约评估（{info.supplier_code}）",
            execute_exceptions=[]
        )]
    }


def _quoted_lines(supplier_code: str) -> List[SupplierPriceLine]:
    """这家供应商在报的物料与报价竞争力（走图谱投影，与比价同种子同事实）。

    逐物料取「全体供应商最低价」算价格分：与 `_rank`/`kg.compare_suppliers`
    同一个定义（最低价 / 本家报价），只是这里以供应商为轴聚合。
    """
    # 与问答链路的图检索共用同一个守卫：熔断器与 MOCK_FAIL_ACTIONS 的判定
    # 只有一处，否则注入「图谱挂了」时两条链路会给出矛盾的事实
    guard()
    store = get_graph_store()
    supplier_id = node_id(NodeKind.SUPPLIER, supplier_code)
    if store.get_node(supplier_id) is None:
        return []

    lines: List[SupplierPriceLine] = []
    for edge in store.neighbors(supplier_id, edge_kind=EdgeKind.SUPPLIES, direction="out"):
        material = store.get_node(edge.dst)
        price = float(edge.props.get("unit_price") or 0.0)
        if material is None or price <= 0:
            continue

        prices = [
            float(e.props.get("unit_price") or 0.0)
            for e in store.neighbors(material.node_id, edge_kind=EdgeKind.SUPPLIES, direction="in")
        ]
        prices = [p for p in prices if p > 0]
        min_price = min(prices) if prices else price

        lines.append(SupplierPriceLine(
            material_code=material.key,
            material_name=str(material.props.get("material_name") or ""),
            unit=str(material.props.get("unit") or ""),
            unit_price=price,
            is_lowest=price <= min_price,
            price_score=round(min_price / price, 4),
        ))

    lines.sort(key=lambda line: line.material_code)
    return lines


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


def _failure(reply: str, detail: str, node: NodeName = NodeName.REPORT) -> Dict:
    """失败结论：追问话术 + code=1 的节点结果（路由与时间线都认它）。"""
    logger.warning(f"供应商链路未完成（{node.value}）：{detail}")
    return {
        SubSupplierStateField.SUPPLIER_QUERY: SupplierComparison(summary=reply),
        SubSupplierStateField.TASK_RESULT: TaskResult(
            task_result=reply, task_result_explain=detail, task_result_code=1
        ),
        SubSupplierStateField.NODE_RESULT: [NodeResult(
            execute_node_name=node.value,
            execute_result_code=1,
            execute_result_msg="目标未定位，已中止（追问中）"
            if node == NodeName.RESOLVE else "供应商查询未完成",
            execute_exceptions=[detail]
        )]
    }
