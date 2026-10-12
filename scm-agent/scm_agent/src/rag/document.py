"""
语料加载：把「知识库」变成一份扁平的文档列表。

**语料只有一种来源：人工维护的 Markdown**（`data/knowledge/**/*.md`）——
制度、规范、FAQ 这类需要反复迭代措辞的内容，直接用文档本身，改完下次构建
索引即生效；目录名即 kind（policies / materials / faq），新增一类语料只需
新建目录。

（早先的版本还有一个来源：把 `data/seed/*.json` 里的结构化商品目录渲染成
商品页文档。换到供应链域后语料全部是制度类 Markdown，那条路径已删除——
留着一份永不命中的加载器，只会在每次建索引时多打一行「目录不存在」的
警告，还会让人以为另有语料没被加载。）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.agent_logger import get_logger

logger = get_logger("rag_document")

# data/ 目录：src/rag/document.py -> src/rag -> src -> scm_agent
# （词法 parents，不写 .resolve()：见 utils/env_loader.py 的说明）
_DATA_DIR = Path(__file__).parents[2] / "data"
KNOWLEDGE_DIR = _DATA_DIR / "knowledge"


class RawDocument:
    """尚未分块的原始文档。"""

    __slots__ = ("doc_id", "title", "text", "source", "kind", "metadata")

    def __init__(self, doc_id: str, title: str, text: str, source: str,
                 kind: str = "policy", metadata: Optional[Dict[str, Any]] = None):
        self.doc_id = doc_id
        self.title = title
        self.text = text
        self.source = source
        self.kind = kind
        self.metadata = metadata or {}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RawDocument {self.doc_id} {len(self.text)}字>"


# ---------------------------------------------------------------- Markdown

def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(_DATA_DIR.parent)).replace("\\", "/")
    except ValueError:  # pragma: no cover - 路径不在项目内时
        return str(path).replace("\\", "/")


def load_markdown_documents(root: Optional[Path] = None) -> List[RawDocument]:
    """读取知识库目录下的全部 Markdown。

    以**目录名**作为 kind（policies / faq / ...），这样新增一类语料只需新建目录，
    不用改代码。
    """
    root = root or KNOWLEDGE_DIR
    if not root.exists():
        logger.warning(f"知识库目录不存在：{root}")
        return []

    docs: List[RawDocument] = []
    for path in sorted(root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning(f"跳过无法读取的知识文档 {path.name}：{e}")
            continue

        title = _first_heading(text) or path.stem
        docs.append(
            RawDocument(
                doc_id=path.stem,
                title=title,
                text=text,
                source=_relative(path),
                kind=path.parent.name if path.parent != root else "knowledge",
            )
        )

    logger.info(f"加载知识文档 {len(docs)} 篇")
    return docs


def _first_heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


# ---------------------------------------------------------------- 汇总

def load_corpus(root: Optional[Path] = None) -> List[RawDocument]:
    """加载全部语料（当前即全部知识文档）。"""
    return load_markdown_documents(root)


def corpus_fingerprint(docs: List[RawDocument]) -> str:
    """语料指纹：内容变了指纹就变，用于索引缓存失效判断。"""
    import hashlib

    digest = hashlib.sha1()
    for doc in sorted(docs, key=lambda d: d.doc_id):
        digest.update(doc.doc_id.encode("utf-8"))
        digest.update(doc.text.encode("utf-8"))
    return digest.hexdigest()[:12]
