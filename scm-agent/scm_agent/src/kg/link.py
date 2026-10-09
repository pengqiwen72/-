r"""
实体链接：把一段自由文本里的说法指到图节点上。

图查询不能「猜」物料。问「有没有替代料」时如果没定位到具体物料，正确的
行为是**不参与**（把问题留给文档通道或转人工），而不是挑一个最像的。
所以这里逐级收紧，三级里任何一级不确定就返回 None：

  1. **编码直命中**：`M\d{6}` / `S\d{3}` / `WH\d{2}`。最硬的一级 ——
     编码是主数据的主键，用户打出来就是要那个东西；
  2. **名称倒排最长匹配**：全名与「核心名」（首个空格前那段，如
     「冷轧钢板 1.2mm」→「冷轧钢板」）两个形态进索引，命中取**最长**的
     一个（长名优先，避免「钢板」压过「冷轧钢板」）；
  3. **字符 bigram 兜底**：说法与名称有部分重合时（「冷轧钢板断料了」），
     用双向覆盖率（min(查中名、名中查)）≥ 0.5 判命中。**并列即弃** ——
     两家分数打平说明这个说法分辨不出是谁，宁可不答。

口语 → 规范术语的翻译（「磕碰」→「破损」）由 rag 层的同义词扩展负责，
本模块只做「文本 → 编码」的匹配：kg 不 import rag，这是分层红线。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from kg.schema import NodeKind, node_id
from kg.store import GraphStore
from utils.tokenize import char_ngrams, normalize

#: 主数据编码：M + 6 位数字（物料）、S + 3 位数字（供应商）、WH + 2 位数字（仓库）。
#: 前后都加边界断言，避免 "XM1000234" 这种更长串被从中间截出一个「编码」。
_CODE_PATTERNS = {
    NodeKind.MATERIAL: re.compile(r"(?<![A-Za-z0-9])[Mm](\d{6})(?!\d)"),
    NodeKind.SUPPLIER: re.compile(r"(?<![A-Za-z0-9])[Ss](\d{3})(?!\d)"),
    NodeKind.WAREHOUSE: re.compile(r"(?<![A-Za-z0-9])[Ww][Hh](\d{2})(?!\d)"),
}

_PREFIXES = {
    NodeKind.MATERIAL: "M",
    NodeKind.SUPPLIER: "S",
    NodeKind.WAREHOUSE: "WH",
}

#: bigram 兜底的覆盖率下限：双向覆盖率取小值后要求 ≥ 0.5。
#: 0.5 是「一半以上的字都对上」——「冷轧钢板断料了」对「冷轧钢板」是
#: min(3/5, 3/3)=0.6 过线；泛问「钢板还有多少」对任何钢板料都 ≤ 0.2，
#: 落在线下（这正是要的：泛问不该被指到一个具体物料上）。
BIGRAM_MIN = 0.5

#: 名称匹配要求的最小长度：两字的名称（如「铜排」）误报率太高，不进索引
_NAME_MIN_CHARS = 3


def extract_codes(text: str, kind: NodeKind) -> List[str]:
    """抽取文本里出现的某类编码（按出现顺序，去重）。"""
    pattern = _CODE_PATTERNS[kind]
    prefix = _PREFIXES[kind]
    out: List[str] = []
    for m in pattern.finditer(text or ""):
        code = f"{prefix}{m.group(1)}"
        if code not in out:
            out.append(code)
    return out


def link_material(text: str, store: GraphStore) -> Optional[str]:
    """把文本指到一个物料编码上；定位不到返回 None。"""
    return _link(text, store, NodeKind.MATERIAL)


def link_supplier(text: str, store: GraphStore) -> Optional[str]:
    return _link(text, store, NodeKind.SUPPLIER)


def link_warehouse(text: str, store: GraphStore) -> Optional[str]:
    return _link(text, store, NodeKind.WAREHOUSE)


# ---------------------------------------------------------------- 内部

def _link(text: str, store: GraphStore, kind: NodeKind) -> Optional[str]:
    codes = extract_codes(text, kind)
    if codes:
        # 编码写出来了就以它为准；图上没有这个节点说明主数据里也没有，
        # 返回 None（查询层会得到空事实），不去猜别的
        for code in codes:
            if store.get_node(node_id(kind, code)) is not None:
                return code
        return None

    return _link_by_name(text, store, kind)


def _link_by_name(text: str, store: GraphStore, kind: NodeKind) -> Optional[str]:
    norm = _squeeze(text)
    if not norm:
        return None

    # ---- 名称倒排：形态 -> 编码（一个形态可能对应多个编码，出现在这里的
    # 都是主数据里的唯一命名，重复命名会在 verify_seed_data 那层被拦） ----
    index: Dict[str, str] = {}
    for node in store.nodes_of_kind(kind):
        for form in _name_forms(_display_name(node.props)):
            # 不同编码共享同一个形态（如两个仓同名）时不入索引，避免随机取一个
            if form in index and index[form] != node.key:
                index[form] = ""
                continue
            index.setdefault(form, node.key)

    hits = [form for form, code in index.items() if code and form in norm]
    if hits:
        # 最长匹配：长名更具体，「冷轧钢板」应压过它里面的「钢板」。
        # 并列最长但指向不同物料 = 分辨不出，弃权（与 bigram 层同一原则）。
        longest = max(len(f) for f in hits)
        top = {index[f] for f in hits if len(f) == longest}
        if len(top) == 1:
            return top.pop()
        return None

    return _link_by_bigrams(norm, index)


def _link_by_bigrams(norm: str, index: Dict[str, str]) -> Optional[str]:
    q = set(char_ngrams(norm, 2))
    if len(q) < 2:
        # 太短的文本做不出有意义的重叠判断，宁可不用
        return None

    scored: List[tuple] = []
    for form, code in index.items():
        if not code:
            continue
        n = set(char_ngrams(form, 2))
        if not n:
            continue
        inter = q & n
        if not inter:
            continue
        score = min(len(inter) / len(q), len(inter) / len(n))
        if score >= BIGRAM_MIN:
            scored.append((round(score, 3), code))

    if not scored:
        return None

    scored.sort(key=lambda t: (-t[0], t[1]))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        # 并列 = 分辨不出，弃权（宁可不答，不猜一个）
        return None
    return scored[0][1]


def _display_name(props: dict) -> str:
    for field in ("material_name", "supplier_name", "warehouse_name"):
        value = props.get(field)
        if value:
            return str(value)
    return ""


def _name_forms(name: str) -> List[str]:
    """一个名称进索引的形态：全名 + 核心名（首个空格前的部分）。"""
    forms: List[str] = []
    full = _squeeze(name)
    if len(full) >= _NAME_MIN_CHARS:
        forms.append(full)
    core = _squeeze(name.split(" ")[0])
    if len(core) >= _NAME_MIN_CHARS and core != full:
        forms.append(core)
    return forms


def _squeeze(text: str) -> str:
    """归一化 + 去掉所有空白：名称里的空格不该影响匹配
    （「冷轧钢板 1.2mm」与「冷轧钢板1.2mm」是同一个人写的两种样子）。"""
    return re.sub(r"\s+", "", normalize(text or ""))
