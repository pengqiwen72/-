"""
向量化层：字符 n-gram TF-IDF。

为什么不用现成的 embedding 服务
--------------------------------
两个理由，一个现实一个设计：

1. **离线零配置是这个 demo 的硬约束。** 调一次 embedding API 就意味着要 API Key、
   要网络、要计费，面试官 clone 下来跑不起来，作品集就废了。

2. **TF-IDF 在这个语料上并不吃亏。** 语料是商品参数与政策条款，查询是「保温多久」
   「能不能刻字」这类**术语高度重合**的问法。这类场景里词法信号的判别力接近甚至
   优于稠密向量；稠密向量的优势在 paraphrase（「杯子能保温多长时间」vs「保温时长」），
   而那部分由 n-gram 与下面的 lexical 通道补上。

真正的稠密向量通过 `VECTOR_STORE=milvus` 接入（见 `index.py`），接口一致，
换成真 embedding 只需替换 `encode()` 的实现。

稀疏表示
--------
用 `dict[token] -> weight` 而不是定长数组：词表规模随语料增长，但单条文本命中的
token 数很少，稀疏字典的点积比稠密数组快一个量级，也更省内存。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Sequence

from utils.tokenize import tokenize


class TfidfEmbedder:
    """基于字符 n-gram 的 TF-IDF 向量化器。

    `fit()` 之后 `encode()` 产出 L2 归一化的稀疏向量，因此余弦相似度就是点积。
    """

    def __init__(self, sublinear_tf: bool = True, min_df: int = 1):
        self.sublinear_tf = sublinear_tf
        self.min_df = min_df
        self.idf: Dict[str, float] = {}
        self.doc_count: int = 0
        self._default_idf: float = 1.0

    # ---------- 训练 ----------

    def fit(self, corpus: Sequence[str]) -> "TfidfEmbedder":
        self.doc_count = len(corpus)
        df: Counter = Counter()

        for text in corpus:
            df.update(set(tokenize(text)))

        # min_df 过滤：只出现一次且极短的 token 往往是噪声（分块切出来的半截词）
        self.idf = {
            tok: math.log((self.doc_count + 1) / (cnt + 1)) + 1.0
            for tok, cnt in df.items()
            if cnt >= self.min_df
        }
        # 未登录词的 idf 取最大值：语料里没见过，反而更有区分度
        self._default_idf = math.log(self.doc_count + 1) + 1.0
        return self

    # ---------- 编码 ----------

    def encode(self, text: str) -> Dict[str, float]:
        """把文本编码成 L2 归一化的稀疏 TF-IDF 向量。"""
        tokens = tokenize(text)
        if not tokens:
            return {}

        counts = Counter(tokens)

        if self.sublinear_tf:
            # 次线性 TF：同一 token 重复 10 次不该比出现 1 次重要 10 倍
            tf = {tok: 1.0 + math.log(cnt) for tok, cnt in counts.items()}
        else:
            tf = {tok: float(cnt) for tok, cnt in counts.items()}

        vec = {
            tok: weight * self.idf.get(tok, self._default_idf)
            for tok, weight in tf.items()
        }

        norm = math.sqrt(sum(w * w for w in vec.values()))
        if norm <= 0:
            return {}
        return {tok: w / norm for tok, w in vec.items()}

    def encode_many(self, texts: Iterable[str]) -> List[Dict[str, float]]:
        return [self.encode(t) for t in texts]

    # ---------- 相似度 ----------

    @staticmethod
    def dot(a: Dict[str, float], b: Dict[str, float]) -> float:
        """稀疏点积。遍历较短的一侧，命中即累加。"""
        if len(a) > len(b):
            a, b = b, a
        return sum(w * b.get(tok, 0.0) for tok, w in a.items())

    def cosine(self, a: Dict[str, float], b: Dict[str, float]) -> float:
        """两个已归一化向量的余弦相似度（= 点积）。"""
        return max(0.0, min(1.0, self.dot(a, b)))

    @property
    def vocab_size(self) -> int:
        return len(self.idf)
