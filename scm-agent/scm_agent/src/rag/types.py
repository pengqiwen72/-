"""
RAG 链路的数据结构。

刻意做成 dataclass 而不是 pydantic 模型：这些对象只在进程内传递，不参与
LangGraph 状态序列化，也不需要校验。用 dataclass 省掉一次校验开销，
同时让「哪些字段是检索中间量、哪些是最终产物」一眼可见。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Chunk:
    """检索的最小单元：一段自包含、可独立引用的文本。"""

    chunk_id: str
    doc_id: str
    doc_title: str                      # 一级标题，如「售后与退换货总则」
    section: str                        # 标题路径，如「售后与退换货总则 > 高金额退款审批」
    text: str                           # 已注入标题上下文的正文（用于检索与展示）
    source: str                         # 相对路径，用于出处展示
    kind: str = "policy"                # product / policy / faq
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """出处的人类可读标签。"""
        return self.section or self.doc_title or self.doc_id

    @property
    def body(self) -> str:
        """剥掉开头的标题路径前缀，只留正文。

        `chunking` 会把标题路径拼进 `text` 做检索增强，但**展示与抽句时不能带着它**——
        否则「售后与退换货总则 > 退款到账时效」会作为一句话被选进答案里，
        因为它在字面上与查询高度重合。检索增强与答案呈现是两个不同的用途，
        这里把两者的边界显式划出来。
        """
        text = self.text
        if self.section:
            for prefix in (f"{self.section}\n", self.section):
                if text.startswith(prefix):
                    return text[len(prefix):].strip()
        head, _, rest = text.partition("\n")
        # 兜底：首行不含句末标点且很短，判定为残留的标题行
        if rest and len(head) <= 60 and not any(ch in head for ch in "。；！？|"):
            return rest.strip()
        return text.strip()

    def snippet(self, limit: int = 160) -> str:
        """正文的压平摘要，用于出处页脚。"""
        body = " ".join(self.body.split())
        return body[:limit] + ("…" if len(body) > limit else "")


@dataclass
class ScoredChunk:
    """带打分的候选片段。dense / lexical / rerank 三段分开留痕，便于排查。

    `preset_raw` 是「候选自带的原始分」——目前只有图事实用它。图路径命中意味着
    「结构上确定的事实」，但它的文本里不一定写着查询词（「调拨建议：从 WH01
    调拨 100 kg 到 WH02」里一个查询词都没有），交给词法重排打分会得到近似 0，
    被任何一句字面巧合压下去。所以图事实自带分，重排层只负责套用同一个
    `x/(x+3)` 饱和映射，保持与文档候选**同量纲、可直接比大小**。
    """

    chunk: Chunk
    dense: float = 0.0                  # 向量余弦相似度
    lexical: float = 0.0                # BM25 原始分（未归一）
    fused: float = 0.0                  # RRF 融合分（只用于排序）
    rerank: float = 0.0                 # 归一化后的重排绝对分，[0,1)
    title_hit: float = 0.0              # 查询词与**文档标题**的重合度，用于判定「查询点名了哪个商品」
    preset_raw: float | None = None     # 候选自带的重排原始分（图事实），非 None 时重排层直接采用

    @property
    def final_score(self) -> float:
        return self.rerank or self.fused


@dataclass
class Citation:
    """回答中引用的一条出处。"""

    index: int                          # 正文里的【出处N】编号
    doc_id: str
    title: str
    section: str
    source: str
    snippet: str
    score: float

    def render(self) -> str:
        from utils.text import citation_path

        located = citation_path(self.title, self.section)
        return f"【出处{self.index}】{located}（{self.source}，相关度 {self.score:.2f}）\n    {self.snippet}"


@dataclass
class RagAnswer:
    """商品咨询链路的最终产物。"""

    answer: str = ""
    citations: List[Citation] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "answered"            # answered / handoff
    reason: str = ""                    # 转人工时说明原因；正常时留空
    matched_terms: List[str] = field(default_factory=list)
    channel: str = "DOC"                # 本次检索通道：DOC / RELATION / BOTH（rag.router.Channel 的值）
    degraded: bool = False              # 是否发生过降级（模型输出被数字校验拦下、或通道降级 L1–L3）

    def citation_footer(self) -> str:
        if not self.citations:
            return ""
        return "参考资料：\n" + "\n".join(c.render() for c in self.citations)

    def render(self) -> str:
        """拼成给人看的完整回答（正文 + 出处页脚）。"""
        parts = [self.answer.strip()]
        footer = self.citation_footer()
        if footer:
            parts.append(footer)
        return "\n\n".join(p for p in parts if p)


def make_chunk_id(doc_id: str, section: str, ordinal: int) -> str:
    """确定性 chunk id —— 同一份语料每次构建得到相同的 id，便于比对与缓存。"""
    raw = f"{doc_id}//{section}//{ordinal}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def top_k_or_none(items: List[ScoredChunk], k: int) -> List[ScoredChunk]:
    return items[:k] if k > 0 else items


def as_optional_float(value: Optional[float]) -> Optional[float]:
    return None if value is None else float(value)
