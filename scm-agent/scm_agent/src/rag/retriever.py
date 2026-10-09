"""
混合召回：稠密（TF-IDF 余弦）+ 词法（BM25），用 RRF 融合。

为什么是两路而不是一路
----------------------
两路的失败模式是互补的：

  * 稠密余弦擅长「词都在、但说法不同」，但**容易被长文档稀释**——一段长文本
    哪怕命中了全部查询词，余弦相似度也可能被无关词拉低。
  * BM25 擅长精确术语命中（"SKU-BT500"、"49 元"），但对同义改写无能为力。

RRF（Reciprocal Rank Fusion）的价值在于**只用排名、不用分数**。两路的分数量纲
完全不同（余弦是 0-1，BM25 是无界正数），直接加权求和就得先做归一化，而归一化
方式本身又是个需要调的参数。RRF 绕开了这件事：

    fused(d) = Σ_r 1 / (k + rank_r(d))

`k` 取 60 是 RRF 原论文的经验值——它把「第 1 名与第 2 名的差距」压得足够平缓，
使得**两路都排中游**的片段能超过**一路第一、另一路没排上**的片段。
对知识库问答来说这个性质正是想要的：两边都认为相关的内容，通常比只有一边
强烈认为相关的内容更可信。

第三路：图谱事实
----------------
双通道问答里还有一路候选来自知识图谱（`kg.query.retrieve`）。它和另外两路
的关系与「稠密 × 词法」完全同构 —— 图事实的文本里不一定写着查询词
（调拨建议句里一个查询词都没有），但它的**名次**是确定的（按路径完整度排）。
这正是 RRF 的适用场景：只取名次，不问分数是怎么来的。

三路的组合方式由通道决定（`CHANNEL_WEIGHTS`）：

    DOC       图权重 0     —— 资料性提问不接图谱，避免「问制度答库存」
    RELATION  图 1.0 / 文档 0.5 —— 关系性提问以图为主，文档降权但仍参与
    BOTH      全部 1.0

图候选用字符串键 `g:<key>` 与语料下标键 `c<idx>` 区分 —— 图事实可能在语料
之外，不能拿下标当身份。权重只影响融合分，不改变候选集合；真正的排序由
重排层的**绝对分**决定（图事实用自带的 `preset_raw`）。
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence

from observability import degrade as degrade_mod
from observability.progress import emit
from rag.embedding import TfidfEmbedder
from rag.router import Channel, classify
from rag.types import Chunk, ScoredChunk
from utils.agent_logger import get_logger
from utils.tokenize import tokenize

logger = get_logger("rag_retriever")

# RRF 平滑常数（原论文经验值）
RRF_K = 60

# 两路各自召回多少候选再融合。取得比最终 top_k 大是为了给 RRF 留出
# 「一路排名靠后、另一路排名靠前」的互补空间。
CANDIDATE_FACTOR = 4

#: 图谱通道最多贡献多少条事实进入融合（截断在图事实按预设分排序之后）。
GRAPH_TAKE = 6

#: 通道 -> (稠密权重, 词法权重, 图谱权重)。见模块文档「第三路」。
CHANNEL_WEIGHTS: Dict[Channel, tuple] = {
    Channel.DOC: (1.0, 1.0, 0.0),
    Channel.RELATION: (0.5, 0.5, 1.0),
    Channel.BOTH: (1.0, 1.0, 1.0),
}


class Bm25:
    """标准 BM25（k1=1.5, b=0.75），不依赖任何第三方库。"""

    def __init__(self, corpus_tokens: Sequence[Sequence[str]],
                 k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_count = len(corpus_tokens)
        self.doc_len = [len(toks) for toks in corpus_tokens]
        self.avg_len = (sum(self.doc_len) / self.doc_count) if self.doc_count else 0.0

        self.tf: List[Counter] = [Counter(toks) for toks in corpus_tokens]

        df: Counter = Counter()
        for toks in corpus_tokens:
            df.update(set(toks))
        self.idf: Dict[str, float] = {
            tok: math.log(1 + (self.doc_count - cnt + 0.5) / (cnt + 0.5))
            for tok, cnt in df.items()
        }

    def score(self, query_tokens: Sequence[str]) -> List[float]:
        scores = [0.0] * self.doc_count
        if not self.doc_count or not self.avg_len:
            return scores

        for tok in query_tokens:
            idf = self.idf.get(tok)
            if idf is None or idf <= 0:
                continue
            for i, tf in enumerate(self.tf):
                freq = tf.get(tok)
                if not freq:
                    continue
                denom = freq + self.k1 * (
                    1 - self.b + self.b * self.doc_len[i] / self.avg_len
                )
                scores[i] += idf * freq * (self.k1 + 1) / denom

        return scores


class HybridRetriever:
    """把 chunk 列表建成可检索的索引，对外只暴露 `search()`。

    `graph_retrieve` 是注入的图谱通道：一个 `(query) -> [GraphChunk]` 的可调用
    对象（`kg.query.retrieve`）。用注入而不是本层直接 import kg —— 检索层因此
    不知道图后端的存在（memory / neo4j / 测试替身皆可），依赖方向保持单向。
    """

    def __init__(self, chunks: Sequence[Chunk],
                 graph_retrieve: Optional[Callable[[str], List]] = None):
        self.chunks: List[Chunk] = list(chunks)
        self.graph_retrieve = graph_retrieve
        self.embedder = TfidfEmbedder()
        self.chunk_tokens: List[List[str]] = [tokenize(c.text) for c in self.chunks]
        self.chunk_vectors: List[Dict[str, float]] = []
        self.bm25: Bm25 = Bm25(self.chunk_tokens)
        self._build()

    def _build(self) -> None:
        self.embedder.fit([c.text for c in self.chunks])
        self.chunk_vectors = self.embedder.encode_many([c.text for c in self.chunks])

    # ---------- 单路召回 ----------

    def dense_scores(self, query: str) -> List[float]:
        qv = self.embedder.encode(query)
        if not qv:
            return [0.0] * len(self.chunks)
        return [self.embedder.dot(qv, cv) for cv in self.chunk_vectors]

    def lexical_scores(self, query: str) -> List[float]:
        return self.bm25.score(tokenize(query))

    # ---------- 融合 ----------

    def search(self, query: str, top_k: int = 5,
               channel: Optional[Channel] = None) -> List[ScoredChunk]:
        """混合召回并返回 top_k 候选（此阶段不做重排）。

        `channel=None` 时内部走 `rag.router.classify`；通道决定图谱通道是否
        接入、以及三路各自的融合权重（见 `CHANNEL_WEIGHTS`）。
        """
        if not query.strip():
            return []

        channel = channel or classify(query)
        w_dense, w_lex, w_graph = CHANNEL_WEIGHTS[channel]

        dense = self.dense_scores(query)
        lexical = self.lexical_scores(query)

        n = len(self.chunks)
        take = min(n, max(top_k * CANDIDATE_FACTOR, top_k))

        dense_rank = _ranks(dense, take)
        lexical_rank = _ranks(lexical, take)

        # ---- 第三路：图谱事实（字符串键，候选可以在语料之外） ----
        graph_candidates: Dict[str, ScoredChunk] = {}
        graph_rank: Dict[str, int] = {}
        if w_graph > 0 and self.graph_retrieve is not None:
            for rank, item in enumerate(self._graph_candidates(query), start=1):
                key = f"g:{item.chunk.metadata['graph_key']}"
                graph_candidates[key] = item
                graph_rank[key] = rank

        # ---- RRF 融合（只用名次，不问分数怎么来的） ----
        fused: Dict[str, float] = {}
        for idx, rank in dense_rank.items():
            key = f"c{idx}"
            fused[key] = fused.get(key, 0.0) + w_dense / (RRF_K + rank)
        for idx, rank in lexical_rank.items():
            key = f"c{idx}"
            fused[key] = fused.get(key, 0.0) + w_lex / (RRF_K + rank)
        for key, rank in graph_rank.items():
            fused[key] = fused.get(key, 0.0) + w_graph / (RRF_K + rank)

        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

        out: List[ScoredChunk] = []
        for key, fused_score in ordered:
            if key.startswith("g:"):
                cand = graph_candidates[key]
                cand.fused = round(fused_score, 6)
                out.append(cand)
            else:
                idx = int(key[1:])
                out.append(
                    ScoredChunk(
                        chunk=self.chunks[idx],
                        dense=round(dense[idx], 6),
                        lexical=round(lexical[idx], 6),
                        fused=round(fused_score, 6),
                    )
                )

        # 通道要图、图有事实，而文档这一侧一条都没命中 ——
        # 本次回答将只依据图谱（回答会带 L2 页脚）。文档侧全空可能是语料为空，
        # 也可能是这个问题的用词与任何制度文档都不沾边，两种都按「未引用制度
        # 文档」如实相告，不区分成因（见 observability/degrade.py 的级别语义）。
        if w_graph > 0 and graph_rank and not (dense_rank or lexical_rank):
            degrade_mod.mark(
                degrade_mod.DegradeLevel.L2,
                "文档通道没有可命中的候选（语料为空或全部未命中）",
            )

        emit(
            "rrf_fuse",
            detail=(
                f"融合 {len(out)} 条进入重排"
                f"（稠密 {len(dense_rank)} · 词法 {len(lexical_rank)}"
                f" · 图谱 {len(graph_rank)}）"
            ),
            channel=channel.value,
        )
        return out

    # ---------- 图谱通道 ----------

    def _graph_candidates(self, query: str) -> List[ScoredChunk]:
        """取图事实并转成候选。图谱异常已在 kg 层兜住，这里再兜一道——
        检索链路不因为「图挂了」而整体失败。"""
        try:
            items = self.graph_retrieve(query) or []
        except Exception as e:  # pragma: no cover - kg.retrieve 设计上不抛
            logger.warning(f"图谱通道异常，本次跳过：{e}")
            degrade_mod.mark(degrade_mod.DegradeLevel.L1, f"图谱通道异常：{e}")
            return []

        ranked = sorted(items, key=lambda c: (-float(c.raw), c.key))[:GRAPH_TAKE]
        return [self._to_scored(item) for item in ranked]

    @staticmethod
    def _to_scored(item) -> ScoredChunk:
        """图事实（`kg.query.GraphChunk`）-> 候选。字段转换在检索层一处完成。

        chunk_id 用 key 的 sha1 —— 与 `make_chunk_id` 同样的确定性要求：
        同一张图重复提问，图事实的 chunk_id 不变，出处页脚可比对、可缓存。
        """
        chunk = Chunk(
            chunk_id="kg" + hashlib.sha1(item.key.encode("utf-8")).hexdigest()[:16],
            doc_id=item.doc_id,
            doc_title=item.doc_title,
            section=item.section,
            text=item.text,
            source=item.source,
            kind=getattr(item, "kind", "graph"),
            metadata={"graph_key": item.key, "hops": getattr(item, "hops", 0)},
        )
        return ScoredChunk(chunk=chunk, preset_raw=float(item.raw))


def _ranks(scores: Sequence[float], take: int) -> Dict[int, int]:
    """取分数最高的 take 个，返回 {下标: 名次}（名次从 1 开始）。"""
    if not scores:
        return {}
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:take]
    # 全 0 的候选没有实际名次，排除掉，否则 RRF 会把「谁都没命中」的片段也捞进来
    return {idx: rank for rank, idx in enumerate(order, start=1) if scores[idx] > 0}
