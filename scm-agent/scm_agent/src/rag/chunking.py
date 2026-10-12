"""
语义分块。

朴素做法是按固定字数硬切，那会把一张表格切成两半、把「大额采购审批」
的小标题和它的正文切开——检索命中后拿到的片段自己都读不通，更没法当出处。

这里按**文档自身的语义边界**切：

  * 标题行是天然边界，`##` 起新块，`###` 作为路径的一级；
  * 表格（连续的 `|` 行）是**原子单位**，绝不从中间切开；
  * 只有单个小节超过 `MAX_CHARS` 时，才在段落边界上切，并保留一段重叠，
    避免答案正好落在切口上。

另外每个 chunk 的正文前面会拼上它的标题路径。这不是为了好看——BM25 与
n-gram 向量都只看字面，标题里的「审批」「安全库存」这类词往往正是查询词，
拼进去等于免费拿到一层上下文增强。
"""

from __future__ import annotations

import re
from typing import List

from rag.document import RawDocument
from rag.types import Chunk, make_chunk_id

# 单个 chunk 的软上限（字符数）。中文里 400 字已足够容纳一节完整规则，
# 再长会稀释向量、也会让出处片段太长。
MAX_CHARS = 400

# 超长小节切分时相邻块的重叠长度
OVERLAP_CHARS = 60

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|")


class _Section:
    """按标题切开后的一节。"""

    __slots__ = ("path", "body")

    def __init__(self, path: List[str], body: List[str]):
        self.path = path
        self.body = body

    @property
    def title_path(self) -> str:
        return " > ".join(p for p in self.path if p)

    def text(self) -> str:
        return "\n".join(self.body).strip()


def split_sections(text: str) -> List[_Section]:
    """把 Markdown 按标题切成小节，维护标题路径栈。"""
    sections: List[_Section] = []
    path: List[str] = []
    body: List[str] = []
    started = False

    def flush():
        # 只保留有正文的小节；纯标题不算一节
        if started and any(line.strip() for line in body):
            sections.append(_Section(list(path), list(body)))

    for line in text.splitlines():
        m = _HEADING.match(line)
        if not m:
            body.append(line)
            continue

        level = len(m.group(1))
        heading = m.group(2).strip()

        # 遇到新标题：先把上一节收掉
        flush()
        started = True
        body = []

        # 维护路径栈：同级或更浅的标题出栈
        path = path[: level - 1]
        while len(path) < level - 1:
            path.append("")
        path.append(heading)

    flush()
    return sections


def _split_long_block(block: str, limit: int = MAX_CHARS,
                      overlap: int = OVERLAP_CHARS) -> List[str]:
    """在段落边界切分超长文本，相邻块保留 overlap 个字符的重叠。

    表格行不参与切分——一段里如果有表格，它整体属于同一块。
    """
    if len(block) <= limit:
        return [block]

    paragraphs = _paragraphs(block)
    out: List[str] = []
    current: List[str] = []
    size = 0

    for para in paragraphs:
        plen = len(para)
        # 单段就超限（比如一张大表）：只能整段成块，硬切表格等于毁掉它
        if plen > limit:
            if current:
                out.append("\n".join(current))
                current, size = [], 0
            out.append(para)
            continue

        if size + plen > limit and current:
            out.append("\n".join(current))
            tail = "\n".join(current)[-overlap:] if overlap else ""
            current = [tail] if tail else []
            size = len(tail)

        current.append(para)
        size += plen

    if current:
        out.append("\n".join(current))

    return [b.strip() for b in out if b.strip()]


def _paragraphs(block: str) -> List[str]:
    """切段落，但把连续表格行粘成一段。"""
    paras: List[str] = []
    buf: List[str] = []
    in_table = False

    for line in block.splitlines():
        is_table = bool(_TABLE_ROW.match(line))
        if is_table:
            buf.append(line)
            in_table = True
            continue

        if in_table:
            paras.append("\n".join(buf))
            buf, in_table = [], False

        if line.strip():
            buf.append(line)
        elif buf:
            paras.append("\n".join(buf))
            buf = []

    if buf:
        paras.append("\n".join(buf))

    return paras


def chunk_document(doc: RawDocument, max_chars: int = MAX_CHARS) -> List[Chunk]:
    """把一篇文档切成若干 Chunk。"""
    chunks: List[Chunk] = []
    ordinal = 0

    for section in split_sections(doc.text):
        path = section.title_path or doc.title
        body = section.text()
        if not body:
            continue

        for piece in _split_long_block(body, limit=max_chars):
            ordinal += 1
            # 标题路径注入正文：让「审批」「调拨」这类查询词能命中所属小节
            text = f"{path}\n{piece}" if path else piece
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(doc.doc_id, path, ordinal),
                    doc_id=doc.doc_id,
                    doc_title=doc.title,
                    section=_display_path(doc, path),
                    text=text,
                    source=doc.source,
                    kind=doc.kind,
                    metadata=dict(doc.metadata),
                )
            )

    return chunks


def _display_path(doc: RawDocument, path: str) -> str:
    """去掉路径首段里的文档标题重复，让出处更短。

    文档的一级标题与 `doc.title` 往往相同，出处的第一段重复一遍没有信息量。
    """
    if not path:
        return doc.title
    parts = [p for p in path.split(" > ") if p]
    if parts and parts[0] == doc.title:
        parts = parts[1:]
    return " > ".join(parts) if parts else doc.title


def chunk_documents(docs: List[RawDocument], max_chars: int = MAX_CHARS) -> List[Chunk]:
    out: List[Chunk] = []
    for doc in docs:
        out.extend(chunk_document(doc, max_chars=max_chars))
    return out
