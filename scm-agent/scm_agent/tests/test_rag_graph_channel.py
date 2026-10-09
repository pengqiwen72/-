"""
守的是双通道检索的接线（`src/rag/`）。

  1. **路由**：三类通道各判各的 —— 文档词不接图谱，关系词接图谱，双中判 BOTH，
     全不中落回 DOC（安全侧）；
  2. **第三路融合**：通道权重只缩放融合分、不改变候选集合（DOC 图权重 0）；
     图事实自带 `preset_raw`，重排层直接采用同一套饱和映射，哪怕文本与查询
     零字面重合也排在前列（这正是 preset 存在的意义）；
  3. **降级**：图谱挂 -> L1 + 页脚；文档不可用 -> L2 + 页脚；`ask()` 是请求
     边界，上一轮的降级不带到下一轮；
  4. **确定性**：图事实的 chunk_id 由 key 派生，同一问题重复问不变。
"""

from __future__ import annotations

import pytest

from kg.query import GraphChunk
from observability import degrade as degrade_mod
from rag.expansion import expand_query
from rag.index import ProductKnowledgeBase
from rag.rerank import LexicalReranker
from rag.retriever import HybridRetriever
from rag.router import Channel, classify
from rag.types import Chunk, ScoredChunk

#: 一条典型的关系类问题：断料 + 替代 + 要货，全程命中关系词、不含文档词
RELATION_Q = "M100023 断料了，有没有可以替代的物料？有货吗？"


# ---------------------------------------------------------------- 路由

def test_classify_three_channels():
    """三类判据各自成立：关系词接图、文档词不接、双中 BOTH、全不中安全侧。"""
    assert classify("采购申请的审批流程是什么？") is Channel.DOC
    assert classify("M100023 断料了，还有替代料吗？") is Channel.RELATION
    assert classify("库存管理制度怎么规定的？") is Channel.BOTH
    # 什么词都不命中 -> DOC（安全侧）：宁可少接一路图，不能见谁都接
    assert classify("你好呀") is Channel.DOC


# ---------------------------------------------------------------- 图事实的分数

def test_preset_raw_is_adopted_and_beats_zero_lexical_match():
    """图事实自带分直接采用 —— 文本与查询零字面重合时也不被词法打成 0。

    两条候选文本完全相同，只有一条带 `preset_raw`：图事实的分数来自路径
    完整度（三跳链 > 两跳），不是词法重合度（「调拨建议：从 WH01 调拨
    100 kg 到 WH02」里一个查询词都没有）。
    """
    text = "调拨建议：从 WH01 调拨 100 支到 WH02（调出仓剩余可用 700 支，仍不低于安全库存 300 支）。"
    graph_chunk = Chunk(
        chunk_id="g1", doc_id="scm-kg", doc_title="供应链知识图谱",
        section="图谱 · 多仓库存分布", text=text,
        source="图谱路径（2 跳）", kind="graph",
    )
    plain_chunk = Chunk(
        chunk_id="c1", doc_id="d1", doc_title="无关文档",
        section="无关文档", text=text, source="misc.md", kind="policy",
    )
    candidates = [
        ScoredChunk(chunk=graph_chunk, preset_raw=5.5),
        ScoredChunk(chunk=plain_chunk),
    ]

    ranked = LexicalReranker([["样"]]).rerank("量子计算的机会", candidates)

    assert ranked[0].preset_raw == 5.5
    assert ranked[0].rerank == pytest.approx(5.5 / 8.5, abs=1e-9), "必须套用同一套饱和映射"
    assert ranked[0].title_hit == 0.0, "图 chunk 的「标题」是图文档名，不该参与商品维度收敛"
    # 同一段文字、没有自带分的那条被词法打了 0 分 —— 图事实若也走词法就会输给它
    assert ranked[1].rerank == 0.0


# ---------------------------------------------------------------- 通道接线

def test_graph_entry_is_only_called_for_relation_channels():
    """DOC 通道连图谱入口都不碰；RELATION 才调用（用探针记录调用）。"""
    calls: list = []

    def spy(query: str):
        calls.append(query)
        return []

    corpus = [Chunk(
        chunk_id="c1", doc_id="d1", doc_title="仓储管理制度",
        section="仓储管理制度", text="仓储管理制度 安全库存的设定与库存盘点流程。",
        source="policies/仓储与库存管理规范.md", kind="policy",
    )]
    retriever = HybridRetriever(corpus, graph_retrieve=spy)

    doc_hits = retriever.search("库存盘点流程是什么？", channel=Channel.DOC)
    assert calls == [], "DOC 通道不该碰图谱入口"
    assert doc_hits and all(c.chunk.kind != "graph" for c in doc_hits)

    retriever.search("安全库存要调整吗？", channel=Channel.RELATION)
    assert calls == ["安全库存要调整吗？"]


def test_channel_weights_scale_fusion_without_changing_candidates():
    """通道权重只动融合分、不动候选集合：RELATION 与 BOTH 的差异恰恰在文档权重。

    RELATION 文档权重 0.5、BOTH 为 1.0 —— 同一个文档候选的融合分正好翻倍；
    图权重两者都是 1.0，图事实的融合分不变。候选集合（有哪些 key）完全一致，
    最终排序交给重排绝对分，不交给权重。
    """
    query = "安全库存的设定与调拨流程"
    doc_chunk = Chunk(
        chunk_id="c1", doc_id="d1", doc_title="仓储管理制度",
        section="仓储管理制度", text="仓储管理制度 安全库存的设定与调拨流程。",
        source="policies/仓储与库存管理规范.md", kind="policy",
    )
    graph_item = GraphChunk(
        key="M100023|stock|distribution",
        text="M100023（冷轧钢板 1.2mm）库存分布：WH01 仓可用 700 kg（低于安全库存）。",
        doc_id="scm-kg", doc_title="供应链知识图谱",
        section="图谱 · 多仓库存分布", source="图谱路径（2 跳）",
        raw=5.0, hops=2,
    )
    retriever = HybridRetriever([doc_chunk], graph_retrieve=lambda _q: [graph_item])

    rel = retriever.search(query, channel=Channel.RELATION)
    both = retriever.search(query, channel=Channel.BOTH)
    doc_only = retriever.search(query, channel=Channel.DOC)

    rel_doc = next(c for c in rel if c.chunk.kind == "policy")
    both_doc = next(c for c in both if c.chunk.kind == "policy")
    rel_graph = next(c for c in rel if c.chunk.kind == "graph")
    both_graph = next(c for c in both if c.chunk.kind == "graph")

    # 图权重在 RELATION / BOTH 下都是 1.0 -> 图事实融合分不变（四舍五入到 6 位）
    assert rel_graph.fused == pytest.approx(both_graph.fused, abs=1e-5)
    # 文档权重 0.5 -> 1.0：同一候选的融合分翻倍（r1 两路都排第 1，各计一次）
    assert both_doc.fused == pytest.approx(2 * rel_doc.fused, abs=1e-5)
    # 候选集合不变；DOC 通道整条图谱不接入
    assert {c.chunk.chunk_id for c in rel} == {c.chunk.chunk_id for c in both}
    assert all(c.chunk.kind != "graph" for c in doc_only)


# ---------------------------------------------------------------- 端到端（真语料 + 真图）

def test_relation_question_surfaces_graph_fact_on_top(knowledge_base):
    """关系类问题里，三跳链图事实（0.667）排在所有文档片段前面。"""
    ranked = knowledge_base.search(RELATION_Q)

    assert ranked
    top = ranked[0]
    assert top.chunk.kind == "graph"
    assert top.chunk.doc_title == "供应链知识图谱"
    assert top.chunk.metadata["graph_key"].startswith("M100023|substitute|M100031")
    assert top.chunk.metadata["hops"] == 3
    assert top.preset_raw == 6.0
    assert top.rerank == pytest.approx(6 / 9, abs=1e-6)


def _doc_question(kb: ProductKnowledgeBase) -> str:
    """从语料里挑一个「标题不含关系词」的文档，用标题造一个纯文档类问题。

    现取而不是硬编：换语料后这条用例的语义（文档问题走纯文档通道）不依赖
    具体是哪个文档，只要求语料里至少有一个标题干净的。
    """
    for chunk in kb.chunks:
        query = f"{chunk.doc_title}怎么规定的？"
        if classify(expand_query(query)[0]) is Channel.DOC:
            return query
    raise AssertionError("语料里找不到纯文档类问题（所有标题都含关系词？）")


def test_doc_question_never_surfaces_graph_chunks(knowledge_base):
    """文档类问题走纯文档通道 —— 避免「问制度答库存」。"""
    query = _doc_question(knowledge_base)
    ranked = knowledge_base.search(query)

    assert ranked, "文档类问题必须在自己的语料里有命中"
    assert all(c.chunk.kind != "graph" for c in ranked)


def test_graph_chunk_ids_are_deterministic(knowledge_base):
    """图事实的 chunk_id 由 key 的 sha1 派生：同一问题重复问，id 不变。"""
    retriever = knowledge_base.retriever

    first = retriever.search(RELATION_Q, channel=Channel.RELATION)
    second = retriever.search(RELATION_Q, channel=Channel.RELATION)

    ids_first = [c.chunk.chunk_id for c in first if c.chunk.kind == "graph"]
    ids_second = [c.chunk.chunk_id for c in second if c.chunk.kind == "graph"]
    assert ids_first and ids_first == ids_second
    assert all(cid.startswith("kg") for cid in ids_first), "图 chunk 的 id 前缀不与语料下标键混用"


# ---------------------------------------------------------------- 降级与请求边界

def test_l1_degrades_with_footer_and_next_request_starts_clean(knowledge_base, monkeypatch):
    """图谱挂掉：L1 + 页脚如实相告；下一轮请求从 L0 起步（reset 在 ask 入口）。"""
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "graph")

    answer = knowledge_base.ask(RELATION_Q)
    assert degrade_mod.current() == degrade_mod.DegradeLevel.L1
    assert answer.degraded is True
    assert "（本次未启用图谱关联分析）" in answer.answer
    assert answer.channel == "RELATION"
    assert all(c.title != "供应链知识图谱" for c in answer.citations), (
        "图谱不可用时不该出现图来源的引用"
    )

    # 故障恢复：页脚消失、降级标记归位 —— 降级状态是请求级的，不是会话级的
    monkeypatch.delenv("MOCK_FAIL_ACTIONS", raising=False)
    again = knowledge_base.ask(RELATION_Q)
    assert degrade_mod.current() == degrade_mod.DegradeLevel.L0
    assert again.degraded is False
    assert "（本次未启用图谱关联分析）" not in again.answer
    assert any(c.title == "供应链知识图谱" for c in again.citations), "恢复后图事实应重新入引用"


def test_l2_footer_when_document_channel_is_empty():
    """文档通道不可用（空语料）：仅凭图事实作答，页脚如实相告。"""
    from kg.query import retrieve as graph_retrieve

    kb = ProductKnowledgeBase(chunks=[], graph_retrieve=graph_retrieve)
    answer = kb.ask(RELATION_Q)

    assert degrade_mod.current() == degrade_mod.DegradeLevel.L2
    assert answer.degraded is True
    assert "（本次未引用制度文档，仅使用图谱事实）" in answer.answer
    assert answer.status == "answered", "结构上确定的事实足够作答，不该被判成转人工"
    assert answer.citations and all(c.title == "供应链知识图谱" for c in answer.citations)
