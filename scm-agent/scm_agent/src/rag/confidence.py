"""
置信度与转人工判定。

三个信号加权，各自解决一个失效场景
----------------------------------
    confidence = 0.65 · top1  +  0.20 · margin  +  0.15 · term_coverage

  * **top1（权重最高）**：最相关片段本身有多强。这是主判据，但它单独用不安全——
    语料里总有一条「碰巧最像」的片段，top1 因此存在一个不随查询变化的底噪。

  * **margin = top1 - top2**：第一和第二名的差距。知识库里**有**答案时，
    正确片段通常明显领先；问题时，前几名会挤在一起。所以小 margin 是
    「其实谁都不太相关」的信号，这是 top1 单独看不出来的。

  * **term_coverage**：查询实词在片段里的覆盖率。挡住一类特定失败——
    查询是「无人机送货有没有规定」，而语料里只有一段讲「送货单登记」的，
    BM25 可能因为共享「送货」二字给个不低的原始分，但覆盖率会很低。

覆盖率一律用**实词**口径（`utils/tokenize.py::content_terms`）：双字组只要
含一个停用字就不算数。字符双字组对检索是好尺子，对覆盖率是漏勺——「的规」、
「度里」这类跨词记号凑巧命中一次就多一分，能把判负线顶穿（实测见
`ASK_COVERAGE_VETO`）。

阈值语义
--------
0.30 是**绝对阈值**，不是相对排名。低于它就走转人工（`status="handoff"`），
而不是硬答一条低置信度的内容。这条兜底能不能真的触发，取决于重排分是不是
绝对分——参见 `rerank.py` 的说明。

取值随业务域重标定过（0.35 -> 0.30）。两条约束把它夹在中间：

  * **下界**：图谱通道的图事实映射后落在 0.40 以上（预设 raw 最低 2.0 ->
    `2/(2+3)` = 0.40）。阈值必须在它之下——图路径命中意味着结构上确定的事实，
    不该反过来被阈值拦成转人工。
  * **上界**：真「资料里没有」的问题由 `ASK_COVERAGE_VETO` 的语义判据拦下
    （见下节：两段实测区间只隔着一次采样，阈值分不开它们）；阈值只需不高于
    文档侧的实测下沿，免得把「确实有依据但措辞偏」的问题误伤成转人工。

阈值可用环境变量 `RAG_CONFIDENCE_THRESHOLD` 调整，便于面试演示时现场改参。
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

from rag.expansion import expand_query
from rag.types import ScoredChunk
from utils.tokenize import content_terms, tokenize

# 三项权重（和为 1.0，保证 confidence 落在 [0,1] 且量纲可比）
W_TOP1 = 0.65
W_MARGIN = 0.20
W_COVERAGE = 0.15

DEFAULT_THRESHOLD = 0.30

#: 判负线：诉求词覆盖率**等于 0** 时直接转人工，不看排序分数。
#:
#: 为什么是「等于 0」而不是某个调出来的阈值
#: ----------------------------------------
#: 这个值实测过，**不可调**。把可回答的问题与资料里根本没有的问题放在一起量：
#:
#:     可回答      0.20  0.29  0.38  0.50  0.50  1.00  1.00  1.00
#:     资料没有    0.00  0.00  0.11  0.13
#:
#: 两段区间只隔着**一次采样**（0.13 与 0.20）—— 把线卡在中间看着能分，
#: 实际上是在「这次量到的边界」上做题，换一批问题就翻车：0.13 那头的
#: 「员工差旅报销标准是多少？」里，「报销」在语料里零星出现过，判「有」
#: 还是「没有」本来就是灰区；0.20 那头的「S007 的履约情况怎么样？」里，
#: 「履约」是货真价实的命中，误伤它就是答不出该答的问题。
#:
#: 所以只保留唯一一个语义明确、不需要调的边界：**一个诉求词都找不到着落**。
#: 判负的问题（「公司制度里有没有无人机送货的规定？」）之所以能拿到 0.62 的
#: top1，全靠「制度」这个指代词与文档名重合（正文头部照刻着文名）——剔除
#: 标题词之后，「无人机」「送货」在语料里一个都没有。这不是「分数不够高」，
#: 是「问的东西那份资料里压根没有」。
#:
#: 早期版本在这条线上还漏过一次：「的规」这类**跨词双字组**凑巧命中，
#: 把覆盖率顶到 0.09，问题就被硬答了。修法在实词口径里（见
#: `utils/tokenize.py::content_terms`）——判据侧不认虚词，检索侧不受影响。
ASK_COVERAGE_VETO = 0.0


def threshold() -> float:
    """读取置信度阈值（每次调用都读环境变量，方便演示时现场调参）。"""
    raw = (os.getenv("RAG_CONFIDENCE_THRESHOLD") or "").strip()
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_THRESHOLD
    return min(max(value, 0.0), 1.0)


def term_coverage(query: str, text: str) -> float:
    """查询实词在文本中的覆盖率。用**实词**口径，避免单字与跨词双字组的噪声。"""
    terms = content_terms(query)
    if not terms:
        terms = list(dict.fromkeys(tokenize(query)))
    if not terms:
        return 0.0
    text_tokens = set(tokenize(text))
    return sum(1 for t in terms if t in text_tokens) / len(terms)


def ask_coverage(query: str, title: str, text: str) -> Optional[float]:
    """**诉求词**覆盖率：剔除指代词之后，问题里真正要问的那部分被覆盖了多少。

    为什么不能只算「查询实词覆盖率」
    --------------------------------
    车间问「公司制度里有没有无人机送货的规定？」时，一句话里有两类词：

      * **指代词**——「公司/制度」。它只说明要翻哪一类资料，几乎没有信息量，
        而且几乎必然与文档名重合（正文头部照刻着文名）；
      * **诉求**——「无人机送货」。这才是要回答的东西。

    混在一起算覆盖率，指代词会把分母撑大、把分数抬高，于是「资料里根本没有
    无人机送货」这件事被完全掩盖：修复前实测正是如此 —— top1 是《供应商管理
    制度》里讲履约评分的一段（0.62），总值 0.45，越过阈值答了一段评分口径。

    判别方式很直接：把与 top1 文档标题重合的词剔掉。剩下的词一个都找不到
    着落，说明问题**只在文档名上像**，实际诉求那份资料里没有。

    用**扩展后**的查询来算，与检索保持一致：系统既然承认「次品 ≡ 不良数量」，
    那么语料里有「不良数量」就等于覆盖了「次品」。同义词表因此也是这条判据的
    安全阀——某类问题被误判转人工时，往表里补一条同义词即可，不用改算法。

    返回 None 表示查询词全是指代词（如「库存」两个字），没有额外诉求可判。
    """
    expanded, _ = expand_query(query)
    terms = content_terms(expanded)
    if not terms:
        terms = list(dict.fromkeys(tokenize(expanded)))
    if not terms:
        return None

    title_tokens = set(tokenize(title or ""))
    ask_terms = [t for t in terms if t not in title_tokens]
    if not ask_terms:
        return None

    text_tokens = set(tokenize(text))
    return sum(1 for t in ask_terms if t in text_tokens) / len(ask_terms)


def compute(ranked: Sequence[ScoredChunk], query: str,
            top_n: int = 3) -> Tuple[float, dict]:
    """计算置信度，返回 (置信度, 各分项)。

    分项一并返回是为了可解释：演示时能说清「这次为什么转人工」，
    而不是只给一个 0.21 让人猜。
    """
    if not ranked:
        return 0.0, {"top1": 0.0, "margin": 0.0, "coverage": 0.0, "reason": "无召回候选"}

    top1 = ranked[0].rerank
    top2 = ranked[1].rerank if len(ranked) > 1 else 0.0
    margin = max(0.0, top1 - top2)
    coverage = term_coverage(query, ranked[0].chunk.text)
    ask_cov = ask_coverage(query, ranked[0].chunk.doc_title, ranked[0].chunk.text)

    detail = {
        "top1": round(top1, 4),
        "top2": round(top2, 4),
        "margin": round(margin, 4),
        "coverage": round(coverage, 4),
        "ask_coverage": None if ask_cov is None else round(ask_cov, 4),
        "threshold": threshold(),
    }

    # 诉求词一个都找不到着落 —— 排序分数再高也不作答。
    # 这类问题的 top1 全靠「指代词撞上文档名」撑起来，硬答必然答非所问。
    if ask_cov is not None and ask_cov <= ASK_COVERAGE_VETO:
        detail["reason"] = (
            "问题里的核心诉求（物料名/文档名以外的关键词）在制度与资料中完全没有出现"
        )
        return 0.0, detail

    score = W_TOP1 * top1 + W_MARGIN * margin + W_COVERAGE * coverage
    return round(score, 4), detail


def should_handoff(confidence: float, limit: float | None = None) -> bool:
    """置信度低于阈值 -> 转人工。"""
    return confidence < (threshold() if limit is None else limit)
