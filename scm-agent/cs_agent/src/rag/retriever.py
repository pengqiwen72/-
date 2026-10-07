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
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Sequence

from rag.embedding import TfidfEmbedder
from rag.types import Chunk, ScoredChunk
from utils.tokenize import tokenize

# RRF 平滑常数（原论文经验值）
RRF_K = 60

# 两路各自召回多少候选再融合。取得比最终 top_k 大是为了给 RRF 留出
# 「一路排名靠后、另一路排名靠前」的互补空间。
CANDIDATE_FACTOR = 4


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
    """把 chunk 列表建成可检索的索引，对外只暴露 `search()`。"""

    def __init__(self, chunks: Sequence[Chunk]):
        self.chunks: List[Chunk] = list(chunks)
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

    def search(self, query: str, top_k: int = 5) -> List[ScoredChunk]:
        """混合召回并返回 top_k 候选（此阶段不做重排）。"""
        if not self.chunks or not query.strip():
            return []

        dense = self.dense_scores(query)
        lexical = self.lexical_scores(query)

        n = len(self.chunks)
        take = min(n, max(top_k * CANDIDATE_FACTOR, top_k))

        dense_rank = _ranks(dense, take)
        lexical_rank = _ranks(lexical, take)

        fused: Dict[int, float] = {}
        for idx, rank in dense_rank.items():
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)
        for idx, rank in lexical_rank.items():
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank)

        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

        out: List[ScoredChunk] = []
        for idx, fused_score in ordered:
            out.append(
                ScoredChunk(
                    chunk=self.chunks[idx],
                    dense=round(dense[idx], 6),
                    lexical=round(lexical[idx], 6),
                    fused=round(fused_score, 6),
                )
            )
        return out


def _ranks(scores: Sequence[float], take: int) -> Dict[int, int]:
    """取分数最高的 take 个，返回 {下标: 名次}（名次从 1 开始）。"""
    if not scores:
        return {}
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:take]
    # 全 0 的候选没有实际名次，排除掉，否则 RRF 会把「谁都没命中」的片段也捞进来
    return {idx: rank for rank, idx in enumerate(order, start=1) if scores[idx] > 0}
