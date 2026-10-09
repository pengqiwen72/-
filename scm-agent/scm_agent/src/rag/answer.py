"""
答案生成与「只负责转述」硬约束。

这一层要解决的是大模型在客服场景里最危险的一类错误：**编造确定性事实**。
模型说错一句「质保 3 年」，客户就可能据此主张权利，而这类错误在文本上
看不出任何异常——语气自信、格式正确、和上下文毫无冲突。

约束的办法不是「提示词里写一句不要编造」。提示词约束是**概率性**的，
在 demo 里看着好用，上线后总会有几个百分点漏过去。这里做的是**确定性校验**：

    1. 先把检索到的片段整理成一张**确定性事实单**（facts block），
       里面是原文照抄的参数、金额、时效；
    2. 模型只拿到事实单和问题，产出回答；
    3. 产出后**逐个数字**校验：回答里出现的每一个数字，都必须能在事实单里
       找到（`verify_no_new_numbers`）；
    4. 任何一个数字找不到依据 —— 丢弃模型输出，回退为事实单的确定性转述。

数字是这类幻觉里**唯一可以机械校验**的部分。措辞、语气、是否答非所问这些
没法自动判，但「凭空多出一个 3 年质保」可以被精确抓住。抓住这一条，
就挡住了绝大多数会引发纠纷的输出。

离线模式下第 2 步不调模型，直接走第 4 步的确定性转述——这也保证了
「离线」与「在线」两条路径在**约束语义上完全一致**，只是措辞来源不同。
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from rag.expansion import expand_query
from rag.types import Citation, RagAnswer, ScoredChunk
from utils.agent_logger import get_logger
from utils.tokenize import normalize, tokenize

logger = get_logger("rag_answer")

#: 最多引用几条出处。3 条足以覆盖「商品参数 + 政策条款」的组合，
#: 再多会让页脚喧宾夺主。
MAX_CITATIONS = 3

#: 数字识别：阿拉伯数字（可带千分位与小数）。中文数字（「三年」）不做校验——
#: 语料里几乎不用中文数字表参数，识别它的误报率反而更高。
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

#: 回答长度上限（字符）。客服回答不该长过一屏。
MAX_ANSWER_CHARS = 320


# ---------------------------------------------------------------- 数字校验

def extract_numbers(text: str) -> List[str]:
    """抽取文本中的阿拉伯数字并归一化（去千分位、去尾随 .0、去前导零）。"""
    out: List[str] = []
    for raw in _NUMBER.findall(text or ""):
        out.append(_normalize_number(raw))
    return out


def _normalize_number(raw: str) -> str:
    value = raw.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    value = value.lstrip("0") or "0"
    return value


def verify_no_new_numbers(answer: str, facts: str) -> Tuple[bool, List[str]]:
    """校验回答里的每个数字都能在事实单里找到。

    返回 (是否通过, 无依据的数字列表)。

    注意这里比的是**数字集合的包含关系**，不是逐位置比对——模型把
    「48 小时内发出」改写成「48 小时左右发货」是允许的，那属于措辞；
    但冒出事实单里没有的「72 小时」就会被拦下。
    """
    allowed = set(extract_numbers(facts))
    used = extract_numbers(answer)

    if not used:
        # 回答里一个数字都没有，说明模型没敢下结论——不拦，交给上层按置信度处理
        return True, []

    offending = [n for n in dict.fromkeys(used) if n not in allowed]
    return (not offending), offending


# ---------------------------------------------------------------- 事实单

def build_facts_block(ranked: Sequence[ScoredChunk], limit: int = MAX_CITATIONS) -> str:
    """把候选片段拼成确定性事实单。

    事实单是**原文照抄**的，不做摘要——一旦在这里改写，后面「回答里的数字
    必须来自事实单」的校验就失去意义了（改写的数字也进了事实单）。
    """
    parts: List[str] = []
    for i, item in enumerate(ranked[:limit], start=1):
        parts.append(f"【资料{i}】{item.chunk.section}\n{item.chunk.snippet(limit=100000)}")
    return "\n\n".join(parts)


def build_citations(ranked: Sequence[ScoredChunk], limit: int = MAX_CITATIONS) -> List[Citation]:
    return [
        Citation(
            index=i,
            doc_id=item.chunk.doc_id,
            title=item.chunk.doc_title,
            section=item.chunk.section,
            source=item.chunk.source,
            snippet=item.chunk.snippet(),
            score=round(item.rerank, 4),
        )
        for i, item in enumerate(ranked[:limit], start=1)
    ]


# ---------------------------------------------------------------- 确定性转述

#: Markdown 强调符号与列表前缀：抽句时要剥掉，否则答案里会带 `**` 和 `- `
_MD_EMPHASIS = re.compile(r"[*_`]+")
_LIST_PREFIX = re.compile(r"^\s*[-•]\s*")
#: 表格分隔行，如 `|---|---|`
_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|$")


def _strip_markup(text: str) -> str:
    text = _MD_EMPHASIS.sub("", text)
    text = _LIST_PREFIX.sub("", text)
    return text.strip()


def _is_question(sent: str) -> bool:
    """判断一句是不是「提问」。

    语料里的问句（FAQ 的 `Q：...？`）不是可以拿去回答客户的内容——
    它和被问的问题字面高度重合，抽取式打分一定会把它排到最前面，
    于是答案变成把客户的问题复述一遍。这类句子必须排除。
    """
    stripped = sent.strip()
    if stripped.endswith(("？", "?")):
        return True
    # 以冒号收尾的是「引出下文」的句子（"以下情形不适用七天无理由："），
    # 它后面跟的表/列表才是内容。单独抽出来是一句没说完的话。
    if stripped.endswith(("：", ":")):
        return True
    return bool(re.match(r"^Q[：:]", stripped, re.IGNORECASE))


#: FAQ 的问答行。中文全角冒号与半角都要认，语料里两种写法都有。
_QA_QUESTION = re.compile(r"^(?:Q|问)[：:]\s*(.+)$", re.IGNORECASE)
_QA_ANSWER = re.compile(r"^(?:A|答)[：:]\s*(.+)$", re.IGNORECASE)


class _Unit:
    """一个可被抽取的句子单元。

    `display` 是最终写进答案的文本，`match` 是参与打分的文本——两者在
    FAQ 场景下不同：答案行本身（`A：审核通过后 1-3 个工作日`）才是要输出的内容，
    但它单独看几乎不含提问词，必须**连着它回答的那个问题一起打分**才排得上来。
    """

    __slots__ = ("display", "match")

    def __init__(self, display: str, match: str = ""):
        self.display = display
        self.match = match or display


def _split_sentences(text: str) -> List[_Unit]:
    """把正文切成可抽取的句子单元。

    两类特殊结构单独处理，它们都有明确的文档格式，硬按标点切会切坏：

      * **表格**：逐行拆开。整张表拿去当回答读不通，拆成
        「质保期：非人为损坏 1 年质保」就自然了。表头行与分隔行跳过。
      * **FAQ**：`Q：...？` 与紧随其后的 `A：...` 绑成一个单元。
        不绑的话，问题行因为字面重合度最高会被单独选中，答案就变成了
        把客户的问题复述一遍；绑起来之后整对参与打分，输出的是答案行。
    """
    lines = [ln.strip() for ln in text.splitlines()]
    units: List[_Unit] = []
    pending_question: str = ""

    for i, block in enumerate(lines):
        if not block:
            continue

        # ---- 表格行 ----
        if block.startswith("|"):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            # 表头行 + 分隔行都不是内容
            if _TABLE_SEP.match(block) or _TABLE_SEP.match(nxt):
                continue
            cells = [_strip_markup(c) for c in block.strip("|").split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                units.append(_Unit(f"{cells[0]}：{' '.join(cells[1:])}"))
            continue

        # ---- FAQ 问句：先存着，等它的答案 ----
        m_q = _QA_QUESTION.match(_strip_markup(block))
        if m_q:
            pending_question = _strip_markup(m_q.group(1))
            continue

        # ---- FAQ 答句：与前面的问句配对 ----
        m_a = _QA_ANSWER.match(_strip_markup(block))
        if m_a and pending_question:
            answer = _strip_markup(m_a.group(1))
            units.append(_Unit(answer, match=f"{pending_question} {answer}"))
            pending_question = ""
            continue

        pending_question = ""

        # ---- 普通文本：按句末标点切 ----
        for sent in re.split(r"(?<=[。！？；!?;])\s*", block):
            sent = _strip_markup(sent)
            if sent and not _is_question(sent):
                units.append(_Unit(sent))

    return units


#: 只收命中率不低于最高命中率这个比例的句子。
#: 0.6 是「确实在讲同一件事」的经验线：再低就会把同段落里相邻但无关的
#: 问答/条款补进来，答案变成几句不连贯的话拼在一起。
RELATIVE_HIT_FLOOR = 0.6


def select_sentences(query: str, ranked: Sequence[ScoredChunk],
                     limit: int = 3) -> List[str]:
    """从候选片段里挑出与查询最相关的句子（抽取式，不改写）。

    两个取舍值得说明：

    1. **按候选排名顺序逐条取材，而不是全局混排打分。** 排名第一的片段通常已经
       完整回答了问题，全局混排会把弱相关片段的零碎句子塞进答案，读起来像是
       把几个商品的参数拼在了一起。只有高排名片段确实凑不出句子时才向下取材。

    2. **只用长度 >= 2 的 token 计命中率。** 单字命中几乎必然发生——中文里
       「退」「到」「时」这类字满地都是，只要按单字算，**任何**句子都能拿到
       一个非零命中率，`hit > 0` 这个过滤条件就等于不存在。这和意图识别里
       「客户说想退货」被单字匹配到闲聊意图是同一类错误，同一个修法。

    3. **与检索用同一份扩展后的查询。** 检索靠「磕碰 -> 破损」的同义词扩展找到了
       破损条款，但抽句若拿原查询打分，那个片段里没有一个字能和「磕碰」对上，
       结果就是「召回到了却抽不出句子」→ 误判为转人工。扩展必须在检索与抽句
       两处一致，否则等于没扩展。
    """
    expanded, _ = expand_query(query)

    terms = [t for t in dict.fromkeys(tokenize(expanded)) if len(t) >= 2]
    if not terms:
        terms = list(dict.fromkeys(tokenize(expanded)))
    if not terms:
        return []
    term_set = set(terms)

    # 先给所有候选片段的句子打分，再全局定阈值。
    # 阈值必须**全局**定：如果按块各自定，弱片段里「矮子里拔将军」的那句
    # 照样能过线，答案尾部就会挂上一条不相关的句子。
    scored_by_chunk: List[List[Tuple[float, str]]] = []
    for item in ranked[:MAX_CITATIONS]:
        scored: List[Tuple[float, str]] = []
        for unit in _split_sentences(item.chunk.body):
            match_tokens = set(tokenize(unit.match))
            if not match_tokens:
                continue
            hit = len(term_set & match_tokens) / len(term_set)
            if hit <= 0:
                continue
            # 命中率为主，同分时短句优先（短句更可能是条款本身而非叙述）
            scored.append((hit - min(len(unit.display), 200) / 4000.0, unit.display))
        scored.sort(key=lambda t: t[0], reverse=True)
        scored_by_chunk.append(scored)

    best = max((s[0][0] for s in scored_by_chunk if s), default=0.0)
    floor = best * RELATIVE_HIT_FLOOR

    picked: List[str] = []
    seen: set = set()

    for scored in scored_by_chunk:
        for score, sent in scored:
            if score < floor:
                break
            key = normalize(sent)[:40]
            if key in seen:
                continue
            seen.add(key)
            picked.append(sent)
            if len(picked) >= limit:
                return picked

    return picked


def restate_from_facts(query: str, ranked: Sequence[ScoredChunk]) -> str:
    """确定性转述：只用事实单里的原句作答，一个字都不改写。

    这是模型输出被丢弃、或离线模式下的兜底答案。它的可信度上限不如模型润色过的
    回答，但它**不会错**——这正是客服场景里需要的取舍。
    """
    sentences = select_sentences(query, ranked)
    if not sentences:
        return ""

    body = "；".join(s.rstrip("。；;") for s in sentences)
    answer = f"根据商品资料：{body}。"
    if len(answer) > MAX_ANSWER_CHARS:
        answer = answer[:MAX_ANSWER_CHARS].rstrip() + "…"
    return answer


# ---------------------------------------------------------------- 主流程

def build_answer(query: str, ranked: Sequence[ScoredChunk], confidence: float,
                 detail: dict, *, handoff: bool,
                 model_text: str | None = None) -> RagAnswer:
    """组装最终回答。

    参数 `model_text` 为真实模型输出；给了就走校验，没过就丢弃并记 `degraded`。
    离线模式传 None，直接走确定性转述。
    """
    citations = build_citations(ranked)
    detail = {**detail, "confidence": confidence}

    if handoff:
        return RagAnswer(
            answer=(
                "抱歉，这个问题我在现有的商品资料里没有找到明确依据，"
                "为避免给您错误信息，我已经为您转接人工客服，"
                "稍后会有专员跟进确认。"
            ),
            citations=[],
            confidence=confidence,
            status="handoff",
            reason=_handoff_reason(detail),
        )

    facts = build_facts_block(ranked)
    degraded = False

    if model_text:
        ok, offending = verify_no_new_numbers(model_text, facts)
        if ok:
            return RagAnswer(
                answer=model_text.strip(),
                citations=citations,
                confidence=confidence,
                status="answered",
                matched_terms=_top_terms(query, ranked),
            )
        logger.warning(
            f"模型输出包含无依据数字 {offending}，已丢弃并回退为事实转述"
        )
        degraded = True

    answer = restate_from_facts(query, ranked)
    if not answer:
        # 抽不出可引用的原句：说明召回到了但内容对不上问题，同样转人工
        return RagAnswer(
            answer=(
                "抱歉，我在商品资料里找到的内容似乎和您的问题不完全对应，"
                "已经为您转接人工客服确认，请稍等。"
            ),
            citations=[],
            confidence=confidence,
            status="handoff",
            reason="召回片段无法支撑对当前问题的回答",
        )

    return RagAnswer(
        answer=answer,
        citations=citations,
        confidence=confidence,
        status="answered",
        matched_terms=_top_terms(query, ranked),
        degraded=degraded,
    )


def _top_terms(query: str, ranked: Sequence[ScoredChunk]) -> List[str]:
    """命中的实词，用来向用户/演示方说明「这次是靠什么命中的」。"""
    if not ranked:
        return []
    terms = [t for t in dict.fromkeys(tokenize(query)) if len(t) >= 2]
    text_tokens = set(tokenize(ranked[0].chunk.text))
    return [t for t in terms if t in text_tokens][:8]


def _handoff_reason(detail: dict) -> str:
    threshold_value = detail.get("threshold")
    # 有明确判定原因时以它为准：`ask_coverage` 判负这种「排序分数不低但诉求
    # 没着落」的情形，用 top1/margin 的话术解释会完全说反。
    if detail.get("reason"):
        return f"{detail['reason']}（置信度 {detail.get('confidence', '')}）"

    parts: List[str] = []
    # 与**实际生效的**阈值比较（而非写死默认值）：阈值可以走环境变量调整，
    # 写死的话调参后这段话术就会和真实判定脱节，说反原因。
    if threshold_value is not None and detail.get("top1", 0) < threshold_value:
        parts.append("最相关片段的相关度偏低")
    if detail.get("coverage", 0) < 0.5:
        parts.append("问题中的关键词在资料里覆盖不足")
    if detail.get("margin", 0) < 0.03:
        parts.append("多个候选片段区分度不足")
    if not parts:
        parts.append("综合置信度低于阈值")
    return "；".join(parts) + (
        f"（置信度 {detail.get('confidence', '')}"
        f" < 阈值 {threshold_value}）" if threshold_value is not None else ""
    )


# ---------------------------------------------------------------- 在线模式的提示词

RAG_ANSWER_PROMPT = """你是电商客服的商品咨询助手。请**严格依据**下面提供的资料回答问题。

【硬性约束】
1. 回答中出现的任何数字（年份、金额、时长、尺寸、比例、编号），都必须**原样**出现在下方资料中；
2. 资料没有提到的信息，直接说"资料中未说明"，不要推测、不要用行业常识补全；
3. 不要承诺资料之外的服务（如额外折扣、加急、延长质保）；
4. 回答控制在 3 句话以内，口语化，直接回答客户的问题。

资料：
-------------------
{facts}
-------------------

客户问题：
-------------------
{question}
-------------------

请直接给出回答，不要复述资料原文，不要输出 JSON。
"""


def build_prompt(query: str, facts: str) -> str:
    return RAG_ANSWER_PROMPT.format(facts=facts, question=query)
