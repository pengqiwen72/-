"""
索引装配与对外入口。

对外只暴露一个 `ProductKnowledgeBase.ask(question) -> RagAnswer`，
把「加载语料 → 分块 → 建索引 → 召回 → 重排 → 置信度 → 生成」这六步封在里面。
调用方（`sub_product_qa` 子图、离线合成器、测试）都只依赖这一个方法。

缓存策略
--------
索引构建是纯 CPU 的确定性过程，同一份语料重复构建纯属浪费——子图每次执行都要
问答，重建索引会让 demo 卡住。所以按**语料指纹**缓存：内容变了指纹就变，
自动失效，不用手动清缓存，也不会有「改了知识库但跑的还是旧索引」的坑。

`VECTOR_STORE` 开关
-------------------
默认 `local`：纯 Python 的 TF-IDF，零依赖、零配置。
设为 `milvus` 时走 `MilvusBackend`（需 `pip install pymilvus`），
用于演示「换真向量库不改业务代码」。**注意**：milvus 后端目前只替换召回通道，
重排与置信度逻辑完全复用——这也是分层的意义。
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

from rag import confidence as confidence_mod
from rag.answer import build_answer, build_facts_block
from rag.chunking import chunk_documents
from rag.document import corpus_fingerprint, load_corpus
from rag.expansion import expand_query
from rag.rerank import LexicalReranker
from rag.retriever import HybridRetriever
from rag.types import Chunk, RagAnswer, ScoredChunk
from utils.agent_logger import get_logger

logger = get_logger("rag_index")

#: 召回阶段取多少候选进入重排
DEFAULT_RECALL_K = 12

#: 重排后保留多少条用于生成答案
DEFAULT_TOP_K = 3

#: 「查询点名了某个商品」的判定线：查询实词与商品名（文档标题）的重合度。
#: 取 0.10 的依据是中文 n-gram 的粒度——查询提到商品名时通常能重合两个
#: 以上的二字组（「保温杯」→「保温」「温杯」），在十余个查询词里就是 0.13 上下；
#: 只是碰巧共享一两个字的查询落在 0.05 以下，不会误触发。
TITLE_SCOPE_MIN = 0.10

# 语料指纹 -> 已构建的知识库（进程内缓存）
_CACHE: Dict[str, "ProductKnowledgeBase"] = {}


class ProductKnowledgeBase:
    """商品知识库。一次构建，多次问答。"""

    def __init__(self, chunks: List[Chunk], fingerprint: str = ""):
        self.chunks = chunks
        self.fingerprint = fingerprint
        self.retriever = HybridRetriever(chunks)
        self.reranker = LexicalReranker(self.retriever.chunk_tokens)

    # ---------- 检索 ----------

    def search(self, query: str, recall_k: int = DEFAULT_RECALL_K,
               top_k: int = DEFAULT_TOP_K) -> List[ScoredChunk]:
        """召回 + 重排，返回按相关度降序的候选。

        查询会先过一层领域同义词扩展：客户说的「磕碰」在知识库里写作「破损」，
        不扩展的话两句话一个字都不重合，检索会命中别的条款还给出不低的分数。
        扩展只作用于检索，置信度里的术语覆盖率仍用原查询算——详见 `expansion.py`。
        """
        expanded, _ = expand_query(query)

        candidates = self.retriever.search(expanded, top_k=recall_k)
        if not candidates:
            return []
        ranked = self.reranker.rerank(expanded, candidates)
        ranked = self._scope_products(ranked)
        return ranked[:top_k]

    # ---------- 商品维度收敛 ----------

    def _scope_products(self, ranked: List[ScoredChunk]) -> List[ScoredChunk]:
        """查询点名了某个商品时，排除**其他商品**的片段。

        多商品知识库里最容易出的事故：客户问「这款保温杯能刻字定制吗」，
        磨豆机的「刻字定制 3 个工作日内发出」因为字面命中太强被召回，答案里
        于是同时出现「不支持刻字定制」和「刻字定制 3 个工作日内发出」——
        两句互相矛盾，而且后一句根本不属于客户问的商品。单看任何一句都像是
        有依据的，这正是这类错误难以自查的原因。

        判据是查询与文档标题的 n-gram 重合度（`ScoredChunk.title_hit`）。
        **只排除商品文档**：政策与 FAQ 是跨商品通用的，客户问某个商品时
        运费规则、售后总则照样适用，把它们一起排除会把真正该引用的条款丢掉。
        """
        products = [c for c in ranked if c.chunk.kind == "product"]
        if len(products) <= 1:
            return ranked

        best = max(products, key=lambda c: c.title_hit)
        if best.title_hit < TITLE_SCOPE_MIN:
            return ranked

        scoped_doc = best.chunk.doc_id
        kept = [
            c for c in ranked
            if c.chunk.kind != "product" or c.chunk.doc_id == scoped_doc
        ]
        dropped = len(ranked) - len(kept)
        if dropped:
            logger.info(
                f"商品维度收敛：查询命中商品「{best.chunk.doc_title}」"
                f"（标题重合度 {best.title_hit:.2f}），排除其他商品片段 {dropped} 条"
            )
        return kept

    # ---------- 问答 ----------

    def ask(self, question: str, *, top_k: int = DEFAULT_TOP_K,
            model_text: Optional[str] = None,
            generator: Optional[Callable[[str, str], str]] = None) -> RagAnswer:
        """回答商品咨询。

        生成环节有两种接法，二选一：

        * `model_text` —— 调用方自己已经调过模型，把文本传进来做校验；
        * `generator`  —— 传一个 `(question, facts) -> str` 的可调用对象，
          由本方法在检索完成、且**判定不需要转人工**之后调用它。

        推荐用 `generator`：转人工的判定只有这里知道，调用方事先调模型等于
        在「反正要转人工」的问题上白烧一次 token；而且事实单是同一份检索结果
        拼出来的，外部先 `facts_for()` 再 `ask()` 会把检索跑两遍。

        离线模式两个都不传，直接走确定性转述——这是默认路径。
        """
        question = (question or "").strip()
        if not question:
            return RagAnswer(answer="", status="handoff", reason="问题为空", confidence=0.0)

        _, injected = expand_query(question)
        ranked = self.search(question, top_k=top_k)
        # 覆盖率用**原查询**算：扩展词是人为注入的，拿来算覆盖率会虚高
        conf, detail = confidence_mod.compute(ranked, question)
        handoff = confidence_mod.should_handoff(conf)
        if injected:
            detail["expanded"] = injected

        logger.info(
            f"问答: {question[:40]} | 扩展 {injected or '无'} | 召回 {len(ranked)} 条 | "
            f"置信度 {conf} (top1={detail.get('top1')} margin={detail.get('margin')} "
            f"coverage={detail.get('coverage')}) | "
            f"{'转人工' if handoff else '直接回答'}"
        )

        # 已判定转人工就不再调模型：模型输出反正会被丢掉
        if generator is not None and not handoff and not model_text:
            try:
                model_text = generator(question, build_facts_block(ranked))
            except Exception as e:
                logger.warning(f"生成失败，回退确定性转述：{e}")

        return build_answer(
            question, ranked, conf, detail, handoff=handoff, model_text=model_text
        )

    def facts_for(self, question: str, top_k: int = DEFAULT_TOP_K) -> str:
        """只取事实单——给「先检索、再调模型、后校验」的外部编排路径用。"""
        return build_facts_block(self.search(question, top_k=top_k))

    # ---------- 诊断 ----------

    def stats(self) -> dict:
        kinds: Dict[str, int] = {}
        for c in self.chunks:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        return {
            "chunks": len(self.chunks),
            "vocab": self.retriever.embedder.vocab_size,
            "by_kind": kinds,
            "fingerprint": self.fingerprint,
        }


# ---------------------------------------------------------------- 工厂

def vector_store_mode() -> str:
    return (os.getenv("VECTOR_STORE") or "local").strip().lower()


def get_knowledge_base(*, force_rebuild: bool = False) -> ProductKnowledgeBase:
    """取知识库（按语料指纹缓存）。"""
    docs = load_corpus()
    fingerprint = corpus_fingerprint(docs)

    if not force_rebuild and fingerprint in _CACHE:
        return _CACHE[fingerprint]

    chunks = chunk_documents(docs)

    if vector_store_mode() in {"milvus", "remote", "http"}:
        chunks = _maybe_reindex_with_milvus(chunks)

    kb = ProductKnowledgeBase(chunks, fingerprint)
    _CACHE[fingerprint] = kb
    logger.info(f"知识库构建完成：{kb.stats()}")

    # 语料变了就把旧指纹的缓存清掉，避免长期运行时内存里堆着废弃索引
    for stale in [k for k in _CACHE if k != fingerprint]:
        _CACHE.pop(stale, None)

    return kb


def reset_cache() -> None:
    """清空缓存（测试与演示用）。"""
    _CACHE.clear()


def _maybe_reindex_with_milvus(chunks: List[Chunk]) -> List[Chunk]:
    """`VECTOR_STORE=milvus` 时的可选重排通道。

    这里刻意做成**失败即降级**：milvus 连不上就打一条 warning 继续用本地检索，
    而不是让整个问答链路挂掉。演示环境下没装 pymilvus 或没起 milvus 服务
    都是常态，不该因此跑不起来。
    """
    try:
        from pymilvus import MilvusClient  # noqa: F401
    except ImportError:
        logger.warning(
            "VECTOR_STORE=milvus 但未安装 pymilvus，回退本地 TF-IDF 检索"
        )
        return chunks

    logger.warning(
        "VECTOR_STORE=milvus 已配置，但本 demo 未内置 milvus 服务地址；"
        "当前仍使用本地 TF-IDF 检索（接口一致，替换 backend 即可接入）"
    )
    return chunks
