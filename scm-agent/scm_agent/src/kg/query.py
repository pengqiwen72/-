"""
三个多跳查询，以及把图路径渲染成「可引用的事实」。

查询在做什么
------------
    替代料推荐    物料 →(SUBSTITUTES)→ 替代料 →(IS_MATERIAL/STOCKED_AT)→ 有货仓
                  →(SUPPLIES)→ 报价最低的供应商        —— 2~3 跳
    供应商比价    物料 ←(SUPPLIES)← 各家报价 + 供应商履约属性 —— 1 跳（星形）
    多仓库存分布  物料 ←(IS_MATERIAL)← 库存快照 →(STOCKED_AT)→ 仓库 —— 2 跳
                  + 由缺口仓/富余仓推出的调拨建议

比价评分公式与 `sub_supplier` 子图**逐字一致**（0.5 价格 + 0.3 准时率 +
0.2 质量）。两处口径必须相同：图谱通道与子图是两个数据来源、一个结论，
Phase 6 有「两路排名必须相同」的比对测试。改公式要同时改两处。

为什么渲染成「陈述句 + 出处」而不是返回结构化字段
--------------------------------------------------
下游（检索、作答、数字校验）只认识「一段带出处的文本」。把图路径渲染成
陈述句，就自动获得三件事：能进事实单、能被抽句、数字天然可溯源 ——
每条陈述里的数字都直接来自节点属性，`verify_no_new_numbers` 这一关
不需要为图谱开特例。

预设分（raw）
-------------
图事实自带分，值域与词法 raw 同量纲，由重排层套同一套 `x/(x+3)` 饱和映射
（见 `rag/types.py::ScoredChunk.preset_raw`）。取值的语义是**路径完整度**，
不是精调出来的权重：

    raw 6.0 -> 0.667   完整的三跳链（替代料 + 有货仓 + 报价）
    raw 5.5 -> 0.647   调拨建议（缺口与富余都算出来了）
    raw 5.0 -> 0.625   比价汇总 / 库存分布（一条事实覆盖全貌）
    raw 3.5 -> 0.538   替代料存在但无库存
    raw 3.0 -> 0.500   单家报价明细
    raw 2.0 -> 0.400   否定性事实（未登记替代料 / 无报价）

全部明显高于置信度阈值（0.30）：图路径命中意味着「结构上确定的事实」，
它不应该输给一次词法上的巧合命中。

降级语义
--------
`retrieve()` 是图谱通道的入口，**永不抛出**：缺驱动、连不上、故障注入、
熔断快速失败，一律 `degrade.mark(L1)` 后返回空列表，把问题让给文档通道
或转人工。故障注入走现成的 `MOCK_FAIL_ACTIONS=graph`（与 mock 端口的
apply 同一开关）；连续失败到阈值（默认 3，`BREAKER_FAIL_THRESHOLD`）后
`get_breaker("graph")` 打开，后续调用在 `guard()` 处快速失败，冷却
（默认 20 秒，`BREAKER_RESET_SECONDS`）结束后放一个探针试探 ——
「前三次真失败、第四次快速失败」正是 `load_degraded` 场景演的东西。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from kg.link import link_material
from kg.schema import (
    EdgeKind,
    GraphStoreUnavailable,
    Node,
    NodeKind,
    node_id,
)
from kg.store import GraphStore, get_graph_store
from observability import degrade as degrade_mod
from observability.progress import emit

#: 图谱通道最多贡献多少条事实。与检索融合层的截断无关 —— 那是 recall_k 的事。
GRAPH_TAKE = 6

# 预设分（与模块文档的表格一一对应）
_RAW_NO_SUBSTITUTE = 2.0
_RAW_SUB_AVAILABLE = 6.0
_RAW_SUB_UNAVAILABLE = 3.5
_RAW_COMPARE_SUMMARY = 5.0
_RAW_QUOTE_DETAIL = 3.0
_RAW_NO_QUOTE = 2.0
_RAW_DISTRIBUTION = 5.0
_RAW_TRANSFER = 5.5

#: 图事实在引用里的身份：所有图事实共用一个「文档」
GRAPH_DOC_ID = "scm-kg"
GRAPH_DOC_TITLE = "供应链知识图谱"


@dataclass
class GraphFact:
    """一条由图路径渲染出的陈述。"""

    key: str        # 稳定标识（同一问题同一图，key 相同）
    text: str       # 陈述句（数字全部来自节点属性）
    section: str    # 出处路径，如「图谱 · 替代料推荐」
    source: str     # 图路径标注，如「图谱路径（3 跳）：M100023 →(替代)→ M100031」
    raw: float      # 预设分，见模块文档
    hops: int = 1


@dataclass
class GraphChunk:
    """给 rag 层转 `Chunk` 用的载体。

    刻意不直接构造 `rag.types.Chunk`：kg 不允许 import rag（分层红线），
    两边字段形状一致，转换由检索层一处完成（`rag/retriever.py`）。
    """

    key: str
    text: str
    doc_id: str
    doc_title: str
    section: str
    source: str
    raw: float
    hops: int = 1
    kind: str = "graph"


# ================================================================ 查询

def find_substitutes(store: GraphStore, material_code: str) -> List[GraphFact]:
    """替代料推荐：替谁、有没有货、哪家供得起。"""
    material = store.get_node(node_id(NodeKind.MATERIAL, material_code))
    if material is None:
        return []

    section = "图谱 · 替代料推荐"
    edges = store.neighbors(material.node_id, edge_kind=EdgeKind.SUBSTITUTES,
                            direction="out")
    if not edges:
        return [GraphFact(
            key=f"{material_code}|substitute|none",
            text=f"{material_code}（{_name(material)}）目前没有登记替代物料。",
            section=section,
            source=f"图谱路径（1 跳）：{material_code} 没有 SUBSTITUTES 边",
            raw=_RAW_NO_SUBSTITUTE,
        )]

    unit = material.props.get("unit") or ""
    facts: List[GraphFact] = []
    for edge in edges:
        alt = store.get_node(edge.dst)
        if alt is None:
            continue

        rows = _stock_rows(store, alt.node_id)
        total = sum(qty for _, _, qty, _ in rows)
        if total > 0:
            rows.sort(key=lambda r: (-r[2], r[0].key))
            item, wh, qty, _ = rows[0]
            availability = (
                f"{wh.key} 仓可用 {qty} {unit}"
                f"（{len(rows)} 个仓合计可用 {total} {unit}）"
            )
            raw = _RAW_SUB_AVAILABLE
            hops = 3
        else:
            availability = "各仓当前无可用库存"
            raw = _RAW_SUB_UNAVAILABLE
            hops = 2

        supplier = _cheapest_supplier(store, alt, unit)
        text = (
            f"{material_code}（{_name(material)}）可由 "
            f"{alt.key}（{_name(alt)}）替代：{availability}"
            + (f"；{supplier}" if supplier else "")
            + "。"
        )
        wh_part = rows[0][1].key if rows and total > 0 else "无"
        facts.append(GraphFact(
            key=f"{material_code}|substitute|{alt.key}",
            text=text,
            section=section,
            source=(
                f"图谱路径（{hops} 跳）：{material_code} →(替代)→ {alt.key} "
                f"→(库存)→ {wh_part}"
            ),
            raw=raw,
            hops=hops,
        ))
    return facts


def compare_suppliers(store: GraphStore, material_code: str) -> List[GraphFact]:
    """供应商比价：一条汇总 + 各家明细，评分公式与 sub_supplier 逐字一致。"""
    material = store.get_node(node_id(NodeKind.MATERIAL, material_code))
    if material is None:
        return []

    section = "图谱 · 供应商比价"
    rows: List[Tuple[Node, float]] = []
    for edge in store.neighbors(material.node_id, edge_kind=EdgeKind.SUPPLIES,
                                direction="in"):
        supplier = store.get_node(edge.src)
        price = float(edge.props.get("unit_price") or 0.0)
        if supplier is None or price <= 0:
            continue
        rows.append((supplier, price))

    if not rows:
        return [GraphFact(
            key=f"{material_code}|compare|none",
            text=f"{material_code}（{_name(material)}）目前没有供应商报价记录。",
            section=section,
            source=f"图谱路径（1 跳）：{material_code} 没有 SUPPLIES 入边",
            raw=_RAW_NO_QUOTE,
        )]

    unit = material.props.get("unit") or ""
    scored = [_score_supplier(supplier, price, rows) for supplier, price in rows]
    scored.sort(key=lambda s: (-s[0], s[1], s[2].key))

    top_score, min_price, best = scored[0]
    max_price = max(price for _, price in rows)
    spread = (max_price / min_price - 1.0) if min_price else 0.0
    summary = (
        f"{material_code}（{_name(material)}）共 {len(scored)} 家供应商报价："
        f"最低 {min_price} 元/{unit}（{_name(best)}），最高 {max_price} 元/{unit}，"
        f"价差 {spread:.1%}；按价格 0.5 + 准时率 0.3 + 质量 0.2 加权，"
        f"综合得分最高的是{_name(best)}（{top_score:.2f}）。"
    )

    facts = [GraphFact(
        key=f"{material_code}|compare|summary",
        text=summary,
        section=section,
        source=f"图谱路径（1 跳）：{len(scored)} 家供应商 →(SUPPLIES)→ {material_code}",
        raw=_RAW_COMPARE_SUMMARY,
    )]
    for rank, (score, price, supplier) in enumerate(scored[:3], start=1):
        on_time = float(supplier.props.get("on_time_rate") or 0.0)
        defect = float(supplier.props.get("defect_rate") or 0.0)
        lead = supplier.props.get("lead_time_days") or 0
        facts.append(GraphFact(
            key=f"{material_code}|compare|{supplier.key}",
            text=(
                f"{supplier.key}（{_name(supplier)}）：报价 {price} 元/{unit}，"
                f"承诺交期 {lead} 天，准时率 {on_time:.0%}、不良率 {defect:.1%}，"
                f"综合得分 {score:.2f}。"
            ),
            section=section,
            source=(
                f"图谱路径（1 跳）：{supplier.key} →(SUPPLIES)→ {material_code}"
                f"（报价 {price} 元/{unit}）"
            ),
            raw=_RAW_QUOTE_DETAIL,
        ))
    return facts


def stock_distribution(store: GraphStore, material_code: str) -> List[GraphFact]:
    """多仓库存分布 + 调拨建议（从余量仓调往缺口仓）。"""
    material = store.get_node(node_id(NodeKind.MATERIAL, material_code))
    if material is None:
        return []

    section = "图谱 · 多仓库存分布"
    rows = _stock_rows(store, material.node_id)
    if not rows:
        return [GraphFact(
            key=f"{material_code}|stock|none",
            text=f"{material_code}（{_name(material)}）目前没有库存记录。",
            section=section,
            source=f"图谱路径（2 跳）：{material_code} 没有库存快照",
            raw=_RAW_NO_QUOTE,
        )]

    unit = material.props.get("unit") or ""
    rows.sort(key=lambda r: r[1].key)
    parts = [
        f"{wh.key} 仓可用 {qty} {unit}" + ("（低于安全库存）" if short else "")
        for _, wh, qty, short in [(r[0], r[1], r[2], r[3]) for r in rows]
    ]
    facts = [GraphFact(
        key=f"{material_code}|stock|distribution",
        text=f"{material_code}（{_name(material)}）库存分布：{'；'.join(parts)}。",
        section=section,
        source=(
            f"图谱路径（2 跳）：{material_code} ←(库存快照)← {len(rows)} 个仓库"
        ),
        raw=_RAW_DISTRIBUTION,
        hops=2,
    )]

    short_rows = [r for r in rows if r[3]]
    rich_rows = [r for r in rows if not r[3]]
    if short_rows and rich_rows:
        short = max(short_rows, key=lambda r: _safety(r[0]) - r[2])
        rich = max(rich_rows, key=lambda r: r[2] - _safety(r[0]))
        gap = _safety(short[0]) - short[2]
        surplus = rich[2] - _safety(rich[0])
        qty = min(gap, surplus)
        if qty > 0:
            facts.append(GraphFact(
                key=f"{material_code}|stock|transfer",
                text=(
                    f"调拨建议：从 {rich[1].key} 调拨 {qty} {unit}到 {short[1].key}"
                    f"（调出仓剩余可用 {rich[2] - qty} {unit}，"
                    f"仍不低于安全库存 {_safety(rich[0])} {unit}）。"
                ),
                section=section,
                source=(
                    f"图谱路径（2 跳）：{rich[1].key}（余量）→ {material_code} "
                    f"→ {short[1].key}（缺口）"
                ),
                raw=_RAW_TRANSFER,
                hops=2,
            ))
    return facts


# ================================================================ 通道入口

#: 关系意图词表 -> 对应查询。一个文本可以命中多类（「断料了有替代吗，替代料有货吗」
#: 同时要替代料与库存分布），按命中顺序依次执行，靠预设分排序收敛。
_RUNNERS: Dict[str, Tuple] = {
    "substitutes": (("替代", "替换", "代用", "断料", "缺料"), find_substitutes, "替代料推荐"),
    "compare": (("比价", "哪家", "报价", "便宜", "价格低", "供应商选择"), compare_suppliers, "供应商比价"),
    "stock": (("库存", "仓库", "有货", "调拨", "安全库存", "备货"), stock_distribution, "多仓库存分布"),
}


def route_relation(text: str) -> Tuple[str, ...]:
    """关系类问题的二级分派：文本命中哪几类图查询。"""
    text = text or ""
    return tuple(
        name for name, (words, _, _) in _RUNNERS.items()
        if any(w in text for w in words)
    )


def retrieve(text: str) -> List[GraphChunk]:
    """图谱通道入口（供检索链路调用）。**永不抛出**，失败降级 L1 返回空。

    熔断记账放在这一层（而不是 `guard` 里）：成功（含「没定位到物料」的
    早退）记成功，真实失败记失败，被熔断挡下的调用**不记账** ——
    否则每次快速失败都会把冷却期往后推，冷却永远不会到期，恢复就演不出来。
    """
    kinds = route_relation(text)
    if not kinds:
        return []

    # 函数级 import：kg 保持轻依赖，不把 integrations 拉进模块导入期
    from integrations.resilience import CircuitOpen, get_breaker

    breaker = get_breaker("graph")
    try:
        guard()
        store = get_graph_store()
        code = link_material(text, store)
        if not code:
            # 「没定位到物料」是这次问题里没有可链接的实体，不是故障：
            # 记成功，别让它把熔断器推向打开
            breaker.record_success()
            emit("graph_link", detail="没有定位到物料，图谱通道本次不参与")
            return []
        emit("graph_link", detail=f"定位到物料 {code}")

        facts: List[GraphFact] = []
        for kind in kinds:
            facts.extend(_RUNNERS[kind][1](store, code))
        facts.sort(key=lambda f: (-f.raw, f.key))
        facts = facts[:GRAPH_TAKE]
        emit(
            "graph_query",
            detail=(
                f"多跳查询 {'、'.join(_RUNNERS[k][2] for k in kinds)}"
                f" · 命中 {len(facts)} 条事实"
            ),
            material_code=code,
            graph_facts=len(facts),
        )
        breaker.record_success()
        return as_chunks(facts)
    except GraphStoreUnavailable as e:
        breaker.record_failure()
        degrade_mod.mark(degrade_mod.DegradeLevel.L1, str(e))
        return []
    except CircuitOpen as e:
        # 熔断挡下的调用：按 L1 记降级，但不算「又一次失败」
        degrade_mod.mark(degrade_mod.DegradeLevel.L1, str(e))
        return []
    except Exception as e:  # 图后端的任何异常都按「图谱不可用」处理，不牵连问答主链路
        breaker.record_failure()
        degrade_mod.mark(degrade_mod.DegradeLevel.L1, f"图谱查询异常：{e}")
        return []


def as_chunks(facts: Sequence[GraphFact]) -> List[GraphChunk]:
    """把图事实包成 chunk 形状（字段与 rag 的 Chunk 对齐，转换在检索层完成）。"""
    return [
        GraphChunk(
            key=f.key,
            text=f.text,
            doc_id=GRAPH_DOC_ID,
            doc_title=GRAPH_DOC_TITLE,
            section=f.section,
            source=f.source,
            raw=f.raw,
            hops=f.hops,
        )
        for f in facts
    ]


# ================================================================ 内部

def guard() -> None:
    """图谱通道的入口守卫：先问熔断器，再看故障注入。

    熔断器在前：确认在坏（连续失败到阈值）时连「这次要不要试一下」都省了，
    直接快速失败 —— 这正是熔断的意义。冷却结束后 `allow()` 放行一个探针，
    探针走到下面的真实查询，失败会重新打开。

    故障注入与 mock 端口的 `chaos.apply()` 是同一个开关的同一套语义 ——
    L1 降级演示用它，不另造强制开关。

    公开（去掉下划线）是因为**不止 `retrieve()` 一个入口**：供应商子图的
    履约评估也直接读图（`sub_supplier_node.py::_quoted_lines`）。两个入口
    必须问同一个守卫 —— 否则 `MOCK_FAIL_ACTIONS=graph` 演示「图谱挂了」时，
    问答链路降级了、履约评估却照常给出价格竞争力，同一次故障两种口径。
    """
    from integrations.mock.chaos import failing_actions
    from integrations.resilience import CircuitOpen, get_breaker

    breaker = get_breaker("graph")
    if not breaker.allow():
        raise CircuitOpen(
            f"图谱服务熔断中：连续 {breaker.consecutive_failures} 次失败，"
            f"快速失败（冷却 {breaker.reset_seconds:g} 秒）"
        )

    if "graph" in failing_actions():
        raise GraphStoreUnavailable(
            "[故障注入] MOCK_FAIL_ACTIONS 命中 graph，模拟图谱服务不可用"
        )


def _stock_rows(store: GraphStore, material_node_id: str) -> List[Tuple]:
    """物料的全部库存快照：[(库存节点, 仓库节点, 可用量, 是否低于安全线)]。"""
    rows: List[Tuple] = []
    for edge in store.neighbors(material_node_id, edge_kind=EdgeKind.IS_MATERIAL,
                                direction="in"):
        item = store.get_node(edge.src)
        if item is None:
            continue
        wh_edges = store.neighbors(item.node_id, edge_kind=EdgeKind.STOCKED_AT,
                                   direction="out")
        warehouse = store.get_node(wh_edges[0].dst) if wh_edges else None
        if warehouse is None:
            continue
        qty = int(item.props.get("qty_available") or 0)
        rows.append((item, warehouse, qty, bool(item.props.get("below_safety"))))
    return rows


def _cheapest_supplier(store: GraphStore, material: Node, unit: str) -> str:
    """某物料的报价最低供应商（第 3 跳）；没有报价时返回空串。"""
    best: Optional[Tuple[Node, float]] = None
    for edge in store.neighbors(material.node_id, edge_kind=EdgeKind.SUPPLIES,
                                direction="in"):
        supplier = store.get_node(edge.src)
        price = float(edge.props.get("unit_price") or 0.0)
        if supplier is None or price <= 0:
            continue
        if best is None or price < best[1]:
            best = (supplier, price)
    if best is None:
        return ""

    supplier, price = best
    lead = supplier.props.get("lead_time_days") or 0
    return (
        f"{_name(supplier)}报价 {price} 元/{unit}、承诺交期 {lead} 天"
    )


def _score_supplier(supplier: Node, price: float, rows: Sequence) -> Tuple[float, float, Node]:
    """单家评分：与 `sub_supplier` 逐字一致的 0.5/0.3/0.2 加权。"""
    min_price = min(p for _, p in rows)
    price_score = min_price / price if price else 0.0
    on_time = float(supplier.props.get("on_time_rate") or 0.0)
    defect = float(supplier.props.get("defect_rate") or 0.0)
    total = 0.5 * price_score + 0.3 * on_time + 0.2 * (1.0 - defect)
    return round(total, 4), price, supplier


def _safety(item: Node) -> int:
    return int(item.props.get("safety_stock") or 0)


def _name(node: Node) -> str:
    for field in ("material_name", "supplier_name", "warehouse_name"):
        value = node.props.get(field)
        if value:
            return str(value)
    return node.key
