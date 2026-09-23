"""
RAG 检索增强链路。

分层（自下而上）：

    document   语料加载：Markdown 知识文档 + 由 SKU 渲染的商品页
    chunking   语义分块：标题为界、表格原子、超长才切且带重叠
    embedding  字符 n-gram TF-IDF，L2 归一，稀疏字典
    retriever  混合召回：稠密余弦 + BM25，RRF 融合
    rerank     BM25 + 覆盖率 + 短语命中，饱和映射成绝对分
    confidence 0.65·top1 + 0.20·margin + 0.15·coverage，低于阈值转人工
    answer     事实单 + 数字校验 + 确定性转述 + 出处

对外只用两个东西：

    from rag import get_knowledge_base
    kb = get_knowledge_base()
    kb.ask("保温杯保温多久？")        # -> RagAnswer

各层的设计取舍都写在对应模块的模块级 docstring 里，这里不重复。
"""

from rag.index import (
    DEFAULT_RECALL_K,
    DEFAULT_TOP_K,
    ProductKnowledgeBase,
    get_knowledge_base,
    reset_cache,
    vector_store_mode,
)
from rag.types import Chunk, Citation, RagAnswer, ScoredChunk

__all__ = [
    "ProductKnowledgeBase",
    "get_knowledge_base",
    "reset_cache",
    "vector_store_mode",
    "DEFAULT_TOP_K",
    "DEFAULT_RECALL_K",
    "Chunk",
    "ScoredChunk",
    "Citation",
    "RagAnswer",
]
