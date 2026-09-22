"""
知识检索层:RAG 检索 + 置信度阈值 + 转人工兜底

设计要点:检索结果经过置信度阈值过滤,未命中或置信度不足时不强行作答,自动转人工兜底。

说明:demo 用 n-gram 余弦相似度模拟向量检索(生产环境为 Milvus + Embedding + Rerank),
检索接口签名与真实实现一致,可直接替换为 Milvus 检索器。
"""
import math
from collections import Counter
from typing import List

from app.models import SourceDoc

# 商品知识库(demo 数据,生产环境为 Milvus 中的向量集合)
KNOWLEDGE_BASE = [
    {
        "doc_name": "商品参数-智能保温杯说明书.md",
        "content": "智能保温杯容量 500ml,内胆 316 不锈钢,保温时长 12 小时,支持温度显示与饮水提醒,杯盖可拆洗,不建议放入洗碗机。",
    },
    {
        "doc_name": "商品参数-儿童安全座椅规格.md",
        "content": "儿童安全座椅适用于 9 个月至 12 岁,ISOFIX 硬接口安装,支持 4 档角度调节,通过 3C 与 ECE R44/04 认证,面料可拆洗。",
    },
    {
        "doc_name": "活动规则-618 大促说明.md",
        "content": "618 活动时间为 6 月 1 日至 6 月 20 日,满 300 减 50,可叠加店铺券;预售商品定金 6 月 1 日支付,尾款 6 月 16 日支付,尾款不支持改地址。",
    },
    {
        "doc_name": "发货规则-配送时效.md",
        "content": "现货商品 48 小时内发货,偏远地区 72 小时;预售商品按活动说明时间发货;大件商品(如安全座椅)由物流专线配送,不支持自提。",
    },
    {
        "doc_name": "保修政策-售后说明.md",
        "content": "商品自签收之日起 7 天无理由退货、15 天质量问题换货、1 年质保;人为损坏不在质保范围;退款 1~3 个工作日原路退回。",
    },
]

# 置信度阈值:低于该值视为"知识库未覆盖",不强行作答
CONFIDENCE_THRESHOLD = 0.18


def _ngrams(text: str, n: int = 2) -> Counter:
    """中文按 2-gram 切分(模拟分词)"""
    text = "".join(ch for ch in text if ch.strip())
    if len(text) < n:
        return Counter([text])
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def retrieve(query: str, top_k: int = 3, threshold: float = CONFIDENCE_THRESHOLD) -> List[SourceDoc]:
    """
    检索知识库并做置信度过滤
    返回:按相似度倒序的 SourceDoc 列表;全部低于阈值时返回空列表(由调用方走兜底)
    """
    q = _ngrams(query)
    scored: List[SourceDoc] = []
    for doc in KNOWLEDGE_BASE:
        score = _cosine(q, _ngrams(doc["doc_name"] + doc["content"]))
        if score >= threshold:
            scored.append(SourceDoc(doc_name=doc["doc_name"], content=doc["content"], score=round(score, 3)))
    scored.sort(key=lambda d: d.score, reverse=True)
    return scored[:top_k]


def best_score(docs: List[SourceDoc]) -> float:
    """当前检索结果的最优相似度(供质量判定使用)"""
    return max((d.score for d in docs), default=0.0)
