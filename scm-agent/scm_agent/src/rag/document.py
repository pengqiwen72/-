"""
语料加载：把「知识库」变成一份扁平的文档列表。

知识库有两个来源，这里刻意用两种不同的处理方式：

1. **人工维护的 Markdown**（`data/knowledge/**/*.md`）——政策、FAQ 这类需要
   反复迭代措辞的内容，直接用文档本身，改完下次构建索引即生效。

2. **结构化的商品目录**（`data/seed/products.json`）——商品参数是结构化数据，
   真正权威的是 SKU 表而不是某段文案。所以这里**在构建索引时把 SKU 渲染成
   商品页**，而不是把商品文案再手工抄一遍。这样商品改价、改参数只需改一处，
   不会出现「知识库说 199、订单系统说 229」这种经典事故。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from utils.agent_logger import get_logger

logger = get_logger("rag_document")

# data/ 目录：src/rag/document.py -> src/rag -> src -> scm_agent
# （词法 parents，不写 .resolve()：见 utils/env_loader.py 的说明）
_DATA_DIR = Path(__file__).parents[2] / "data"
KNOWLEDGE_DIR = _DATA_DIR / "knowledge"
SEED_DIR = _DATA_DIR / "seed"


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


# ---------------------------------------------------------------- 商品目录

#: 商品页渲染顺序。用列表而不是遍历 dict，是为了让同一 SKU 每次生成的文本完全一致
#: ——否则索引重建后 chunk 内容漂移，引用的可比性就没了。
_SECTION_LABELS = {
    "specs": "规格参数",
    "warranty": "质保说明",
    "shipping": "发货与运费",
    "after_sale": "售后政策",
}


def render_product_page(sku: Dict[str, Any]) -> str:
    """把一个 SKU 字典渲染成商品页 Markdown。"""
    lines: List[str] = []
    name = sku.get("product_name", "")
    lines.append(f"# {name}")
    lines.append("")
    lines.append(
        f"商品编号：{sku.get('sku_id', '')}　品牌：{sku.get('brand', '')}　"
        f"分类：{sku.get('category', '')}　售价：{sku.get('price', '')} 元"
    )
    lines.append("")

    for key, label in _SECTION_LABELS.items():
        block = sku.get(key) or {}
        if not isinstance(block, dict) or not block:
            continue
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| 项目 | 说明 |")
        lines.append("|---|---|")
        for k, v in block.items():
            lines.append(f"| {k} | {_stringify(v)} |")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _stringify(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value)
    return str(value)


def load_product_documents(seed_dir: Optional[Path] = None) -> List[RawDocument]:
    """把 `products.json` 里的 SKU 渲染成商品页文档。"""
    seed_dir = seed_dir or SEED_DIR
    path = seed_dir / "products.json"
    if not path.exists():
        logger.warning(f"商品目录不存在：{path}")
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"商品目录解析失败：{e}")
        return []

    items: Iterable[Dict[str, Any]] = payload.get("products", []) if isinstance(payload, dict) else payload

    docs: List[RawDocument] = []
    for sku in items:
        if not isinstance(sku, dict) or not sku.get("sku_id"):
            continue
        docs.append(
            RawDocument(
                doc_id=str(sku["sku_id"]),
                title=str(sku.get("product_name", sku["sku_id"])),
                text=render_product_page(sku),
                source=_relative(path),
                kind="product",
                metadata={
                    "sku_id": sku["sku_id"],
                    "category": sku.get("category", ""),
                    "brand": sku.get("brand", ""),
                    "price": sku.get("price", ""),
                },
            )
        )

    logger.info(f"渲染商品页 {len(docs)} 篇")
    return docs


# ---------------------------------------------------------------- 汇总

def load_corpus(root: Optional[Path] = None,
                seed_dir: Optional[Path] = None) -> List[RawDocument]:
    """加载全部语料（知识文档 + 商品页）。"""
    return load_markdown_documents(root) + load_product_documents(seed_dir)


def corpus_fingerprint(docs: List[RawDocument]) -> str:
    """语料指纹：内容变了指纹就变，用于索引缓存失效判断。"""
    import hashlib

    digest = hashlib.sha1()
    for doc in sorted(docs, key=lambda d: d.doc_id):
        digest.update(doc.doc_id.encode("utf-8"))
        digest.update(doc.text.encode("utf-8"))
    return digest.hexdigest()[:12]
