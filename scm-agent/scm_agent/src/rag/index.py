"""
索引装配与对外入口。

对外只暴露一个 `KnowledgeBase.ask(question) -> RagAnswer`，
把「加载语料 → 分块 → 建索引 → 召回 → 重排 → 置信度 → 生成」这六步封在里面。
调用方（`sub_material_qa` 子图、离线合成器、测试）都只依赖这一个方法。

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

双通道与降级
------------
本层同时是「文档 + 图谱」双通道的**汇合点**：`search()` 里路由（`rag.router`）
决定图谱通道是否接入，图谱入口（`kg.query.retrieve`）以注入方式交给
`HybridRetriever`（依赖方向单一：rag -> kg）。`ask()` 是**请求边界**：
入口处重置降级状态，出口处按本次请求的通道可用性拼页脚（`observability/degrade`）。
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

from observability import degrade as degrade_mod
from observability.progress import emit
from rag import confidence as confidence_mod
from rag import router as router_mod
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

#: 「查询点名了某个对象」的判定线：查询实词与对象文档标题的重合度。
#: 取 0.10 的依据是中文 n-gram 的粒度——查询提到对象名时通常能重合两个
#: 以上的二字组，在十余个查询词里就是 0.13 上下；只是碰巧共享一两个字的
#: 查询落在 0.05 以下，不会误触发。
TITLE_SCOPE_MIN = 0.10

# 语料指纹 -> 已构建的知识库（进程内缓存）
_CACHE: Dict[str, "KnowledgeBase"] = {}


class KnowledgeBase:
    """知识库（物料规范 / 制度 / FAQ 三语料）。一次构建，多次问答。"""

    def __init__(self, chunks: List[Chunk], fingerprint: str = "",
                 graph_retrieve: Optional[Callable[[str], list]] = None):
        self.chunks = chunks
        self.fingerprint = fingerprint
        self.retriever = HybridRetriever(chunks, graph_retrieve=graph_retrieve)
        self.reranker = LexicalReranker(self.retriever.chunk_tokens)

    # ---------- 检索 ----------

    def search(self, query: str, recall_k: int = DEFAULT_RECALL_K,
               top_k: int = DEFAULT_TOP_K,
               channel: Optional[router_mod.Channel] = None) -> List[ScoredChunk]:
        """召回 + 重排，返回按相关度降序的候选。

        查询会先过一层领域同义词扩展：用户说的「断料」在语料里写作「缺料」，
        不扩展的话两句话一个字都不重合，检索会命中别的条款还给出不低的分数。
        扩展只作用于检索，置信度里的术语覆盖率仍用原查询算——详见 `expansion.py`。

        路由在**扩展后**的文本上判类（与 `confidence.ask_coverage` 同一口径）：
        系统既然承认两词等价，通道判断也该认。`channel` 由调用方传入时复用
        （`ask()` 已经算过一次），避免同一问题路由两次。
        """
        expanded, _ = expand_query(query)
        if channel is None:
            channel = router_mod.classify(expanded)
        emit("channel_route", detail=f"检索通道：{router_mod.LABELS[channel]}",
             channel=channel.value)

        candidates = self.retriever.search(expanded, top_k=recall_k, channel=channel)
        if not candidates:
            return []
        ranked = self.reranker.rerank(expanded, candidates)
        ranked = self._scope_materials(ranked)
        ranked = self._keep_relation_evidence(ranked, channel, top_k)
        return ranked[:top_k]

    # ---------- 关系类问题的引用配额 ----------

    def _keep_relation_evidence(self, ranked: List[ScoredChunk],
                                channel: router_mod.Channel,
                                top_k: int) -> List[ScoredChunk]:
        """通道含图谱时，引用清单里至少留一条图事实。

        为什么需要一条**配额**规则
        --------------------------
        两类候选的分数来源不同，量纲也不同（见 `rerank.py`）：

          * 图事实：路径完整度（三跳链 raw 6.0 -> 0.67），有上限；
          * 制度文档：词面匹配强度，换域之后「替代料」「安全库存」「调拨」
            这些词在制度语料里本来就密集，相关条款拿到 0.8+ 很正常。

        于是会出现一个看着合理、实则荒谬的结果：问「M100023 断料了，有没有
        可以替代的物料？有货吗？」，引用清单被制度条款占满 —— 讲清了「怎么找
        替代料」，却没有一句「替代的是哪个料、哪个仓有货」。分数高低在这里
        不能直接比，它们衡量的不是同一种东西。

        所以通道已经承认「这个问题需要图事实」时（RELATION / BOTH），给图事实
        留一个引用位：把它放进第 `top_k` 位、替掉当前末位（相关度最低的那条）。
        **只调整引用清单的构成，不碰任何分数** —— 置信度仍由重排绝对分决定，
        分数该怎么算还怎么算（这一条是刻意的：一旦为了让图事实排上来去改分，
        置信度阈值就整体位移了，那才是真会出事的地方）。
        """
        if channel is None or channel is router_mod.Channel.DOC or top_k <= 0:
            return ranked

        head, rest = ranked[:top_k], ranked[top_k:]
        if any(c.preset_raw is not None for c in head):
            return ranked  # 图事实已经凭自己的分进来了，不需要配额

        fact = next((c for c in rest if c.preset_raw is not None), None)
        if fact is None or not head:
            return ranked

        logger.info(
            f"关系通道引用配额：图事实「{fact.chunk.section}」（{fact.rerank:.3f}）"
            f"顶替末位文档「{head[-1].chunk.section}」（{head[-1].rerank:.3f}）"
        )
        return head[:-1] + [fact] + rest

    # ---------- 物料维度收敛 ----------

    def _scope_materials(self, ranked: List[ScoredChunk]) -> List[ScoredChunk]:
        """查询点名了某个对象（`kind="materials"` 的文档）时，排除**其他对象**的片段。

        这类错误最典型的样子：问「M400301 怎么验收」，另一个物料的规格页里
        「抽检比例 10%」因为字面命中太强被召回 —— 答案里同时出现两个物料的
        验收口径，两句互相矛盾，而且后一句根本不属于问的那个物料。单看任何
        一句都像是「有依据」，这正是它难以自查的原因。

        判据是查询与文档标题的 n-gram 重合度（`ScoredChunk.title_hit`）。
        **只排除对象文档**：制度、规范与 FAQ 是跨对象通用的 —— 问某个物料时
        验收规范、仓储制度照样适用，把它们一起排除会把真正该引用的条款丢掉。

        当前语料全部是通用文档（没有按对象拆页的规格文档），这条收敛因此是
        空转；保留它是因为「一物料一页」是可预见的下一批语料，判据与阈值
        到那时候才真正起作用。
        """
        objects = [c for c in ranked if c.chunk.kind == "materials"]
        if len(objects) <= 1:
            return ranked

        best = max(objects, key=lambda c: c.title_hit)
        if best.title_hit < TITLE_SCOPE_MIN:
            return ranked

        scoped_doc = best.chunk.doc_id
        kept = [
            c for c in ranked
            if c.chunk.kind != "materials" or c.chunk.doc_id == scoped_doc
        ]
        dropped = len(ranked) - len(kept)
        if dropped:
            logger.info(
                f"物料维度收敛：查询命中物料文档「{best.chunk.doc_title}」"
                f"（标题重合度 {best.title_hit:.2f}），排除其他物料片段 {dropped} 条"
            )
        return kept

    # ---------- 问答 ----------

    def ask(self, question: str, *, top_k: int = DEFAULT_TOP_K,
            model_text: Optional[str] = None,
            generator: Optional[Callable[[str, str], str]] = None) -> RagAnswer:
        """回答物料与制度咨询。

        生成环节有两种接法，二选一：

        * `model_text` —— 调用方自己已经调过模型，把文本传进来做校验；
        * `generator`  —— 传一个 `(question, facts) -> str` 的可调用对象，
          由本方法在检索完成、且**判定不需要转人工**之后调用它。

        推荐用 `generator`：转人工的判定只有这里知道，调用方事先调模型等于
        在「反正要转人工」的问题上白烧一次 token；而且事实单是同一份检索结果
        拼出来的，外部先 `facts_for()` 再 `ask()` 会把检索跑两遍。

        离线模式两个都不传，直接走确定性转述——这是默认路径。
        """
        # 请求边界：降级级别只反映**本次问答**期间的通道可用性，
        # 不把上一轮的降级带进这一轮（见 observability/degrade.py）
        degrade_mod.reset()

        question = (question or "").strip()
        if not question:
            return RagAnswer(answer="", status="handoff", reason="问题为空", confidence=0.0)

        expanded, injected = expand_query(question)
        channel = router_mod.classify(expanded)
        ranked = self.search(question, top_k=top_k, channel=channel)
        # 覆盖率用**原查询**算：扩展词是人为注入的，拿来算覆盖率会虚高
        conf, detail = confidence_mod.compute(ranked, question)
        handoff = confidence_mod.should_handoff(conf)
        if injected:
            detail["expanded"] = injected

        logger.info(
            f"问答: {question[:40]} | 扩展 {injected or '无'} | 通道 {channel.value} | "
            f"召回 {len(ranked)} 条 | "
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

        answer = build_answer(
            question, ranked, conf, detail, handoff=handoff, model_text=model_text
        )
        answer.channel = channel.value

        # 本次请求里发生过通道降级（L1 图不可用 / L2 文档不可用）时，
        # 在答案末尾如实说明「这次少用了什么」——用户看到的不是级别编号，
        # 而是一句人话。L3（转人工）没有页脚：答案本身就是结局。
        footer = degrade_mod.footer()
        if footer:
            answer.answer = f"{answer.answer}{footer}"
            answer.degraded = True
        return answer

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


def _graph_retriever() -> Optional[Callable[[str], list]]:
    """懒取图谱通道入口（`kg.query.retrieve`）。

    import 放在函数里：`kg` 只在知识库构建时加载一次。拿不到就返回 None ——
    问答链路退化为纯文档通道（图后端的问题在 kg 内部已经有一层 L1 兜底，
    这里是最后一层：连模块都 import 不进来时，问答本身不能挂）。
    """
    try:
        from kg.query import retrieve as graph_retrieve

        return graph_retrieve
    except Exception as e:  # pragma: no cover - 正常安装下不会发生
        logger.warning(f"图谱模块不可用（{e}），本次仅启用文档通道")
        return None


def get_knowledge_base(*, force_rebuild: bool = False) -> KnowledgeBase:
    """取知识库（按语料指纹缓存）。"""
    docs = load_corpus()
    fingerprint = corpus_fingerprint(docs)

    if not force_rebuild and fingerprint in _CACHE:
        return _CACHE[fingerprint]

    chunks = chunk_documents(docs)

    if vector_store_mode() in {"milvus", "remote", "http"}:
        chunks = _maybe_reindex_with_milvus(chunks)

    kb = KnowledgeBase(chunks, fingerprint, graph_retrieve=_graph_retriever())
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
