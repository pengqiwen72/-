"""
重排层：对召回候选做词法重排，并压成**绝对分**。

这里最关键的一个设计决定是「绝对分」而不是「归一化分」
------------------------------------------------------
一个很容易踩的坑：重排后把分数按当前 query 的结果做 min-max 归一化。
那样**每次查询的第一名都必然是 1.0**——哪怕这次一条都没真正匹配上，
排在最前面的那条垃圾片段也会得到满分。

后果是置信度彻底失去意义：固定阈值（比如 0.35）永远会被突破，
「检索不到就转人工」这条兜底逻辑**永远不会触发**。这个 bug 很隐蔽，
因为单看某一次查询的结果，分数分布看起来很合理。

所以这里用 `raw / (raw + 3)` 做饱和映射：
    raw = 1  -> 0.25
    raw = 3  -> 0.50
    raw = 9  -> 0.75
    raw = 30 -> 0.91
分数反映的是**这条片段本身的匹配强度**，与本次其他候选无关。阈值因此可比、可调。

重排信号
--------
在候选集上重算 BM25（idf 沿用全语料的，保证不同查询之间可比），再叠加两个信号：

  * **术语覆盖率**：查询里的实词有多少出现在片段中。BM25 会让一个高频词反复
    贡献分数，覆盖率则要求「查询提到的点片段基本都碰到了」，更贴合问答场景。
  * **连续短语命中**：查询中有 2 字以上的连续片段原样出现在文本里，说明不是
    零散关键词巧合。这一项权重给得高，因为它几乎是「命中」的最强证据。
"""

from __future__ import annotations

from typing import List, Sequence

from rag.retriever import Bm25
from rag.types import ScoredChunk
from utils.tokenize import tokenize

#: 饱和映射的分母常数。取 3 的依据：语料实测中「一条真正相关的政策条款」
#: BM25 原始分大致落在 3-10 区间，用 3 作分母能让这个区间铺开在 0.5-0.77，
#: 既不会全部挤在 1.0 附近，也能让明显不相关的片段（raw < 1）落到 0.25 以下。
SQUASH = 3.0

# 三个信号的权重
W_BM25 = 1.0
W_COVERAGE = 1.6
W_PHRASE = 2.2

# 短语命中的最小长度：单字命中太容易，不足以作为证据
MIN_PHRASE_LEN = 2


class LexicalReranker:
    """基于 BM25 + 覆盖率 + 短语命中的重排器。"""

    def __init__(self, corpus_tokens: Sequence[Sequence[str]]):
        # 用全语料建 idf，候选集只提供 tf——这样同一片段在不同查询下的
        # BM25 分是可比的，不会因为「本次候选集恰好很小」而虚高。
        self.reference = Bm25(corpus_tokens)

    def rerank(self, query: str, candidates: List[ScoredChunk]) -> List[ScoredChunk]:
        if not candidates:
            return []

        query_tokens = tokenize(query)
        # 去重后的实词：用于算覆盖率，重复词不该重复计分
        query_terms = [t for t in dict.fromkeys(query_tokens) if len(t) >= 2] or list(
            dict.fromkeys(query_tokens)
        )
        query_norm = "".join(query.split())

        for cand in candidates:
            tokens = tokenize(cand.chunk.text)
            cand.rerank = self._score(
                query_terms, tokens, cand.chunk.text, query_norm
            )
            # 单独记录「查询与文档标题的重合度」。它不参与打分——打分改变会
            # 让置信度阈值整体位移，这里只需要一个诊断量，供上层判断
            # 「客户问的是哪个商品」（见 `rag.index` 的 `_scope_products`）。
            cand.title_hit = self._title_hit(query_terms, cand.chunk.doc_title)

        # 分数相同时用 fused 兜底，保证排序稳定（否则不同轮次结果会抖）
        return sorted(candidates, key=lambda c: (c.rerank, c.fused), reverse=True)

    def _score(self, query_terms: Sequence[str], tokens: Sequence[str],
               text: str, query_norm: str) -> float:
        if not query_terms:
            return 0.0

        token_set = set(tokens)

        # 1) BM25（在当前片段上算 tf，idf 用全语料）
        bm25 = self._bm25_single(query_terms, tokens)

        # 2) 术语覆盖率：查询里的实词命中了几成
        hit = sum(1 for term in query_terms if term in token_set)
        coverage = hit / len(query_terms)

        # 3) 连续短语命中：查询里长度 >= 2 的连续子串是否原样出现在文本中
        phrase = self._phrase_bonus(query_norm, "".join(text.split()))

        raw = bm25 * W_BM25 + coverage * W_COVERAGE + phrase * W_PHRASE
        if raw <= 0:
            return 0.0

        return raw / (raw + SQUASH)

    @staticmethod
    def _title_hit(query_terms: Sequence[str], title: str) -> float:
        """查询实词里有几成出现在文档标题中。

        商品页的标题就是商品名，客户提到商品时用词通常与商品名重合
        （「保温杯」命中「轻量智能保温杯 PRO 500ml」），这个比例因此可以
        当作「客户问的是这个商品」的判断依据。
        """
        if not query_terms or not title:
            return 0.0
        title_tokens = set(tokenize(title))
        if not title_tokens:
            return 0.0
        hit = sum(1 for term in query_terms if term in title_tokens)
        return hit / len(query_terms)

    def _bm25_single(self, query_terms: Sequence[str], tokens: Sequence[str]) -> float:
        from collections import Counter

        if not tokens:
            return 0.0

        tf = Counter(tokens)
        dl = len(tokens)
        avg = self.reference.avg_len or dl
        k1, b = self.reference.k1, self.reference.b

        score = 0.0
        for term in query_terms:
            freq = tf.get(term)
            if not freq:
                continue
            idf = self.reference.idf.get(term, 0.0)
            if idf <= 0:
                continue
            denom = freq + k1 * (1 - b + b * dl / avg)
            score += idf * freq * (k1 + 1) / denom
        return score

    def _phrase_bonus(self, query_norm: str, text_norm: str) -> float:
        """查询中长度 >= MIN_PHRASE_LEN 的连续子串原样命中的比例。"""
        if len(query_norm) < MIN_PHRASE_LEN:
            return 0.0

        n = len(query_norm)
        # 逐长度递增地找最长命中，命中一次就跳过该段，避免同一段被重复计分
        i = 0
        hits = 0
        total = 0
        while i < n:
            best = 0
            for j in range(i + MIN_PHRASE_LEN, n + 1):
                if query_norm[i:j] in text_norm:
                    best = j - i
                else:
                    # 子串再长也不可能命中（连续性被破坏）
                    break
            if best:
                hits += best
                i += best
            else:
                total += 1
                i += 1

        if hits <= 0:
            return 0.0

        covered = hits / n
        return covered
