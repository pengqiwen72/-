"""
轻量中文友好分词，供离线合成器与 RAG 检索共用。

设计取舍
--------
中文检索常见做法是上 jieba 分词，但那会引入额外依赖，且对「保温杯」「七天无理由」
这类领域词反而容易切碎。这里改用**字符 n-gram**：

  * CJK 连续串 -> 单字 + 相邻双字（"保温杯" -> 保, 温, 杯, 保温, 温杯）
  * 拉丁/数字串 -> 整串 + 子串（"SKU-BT500" -> sku, bt500, sku bt500）

配合 IDF 加权后，单字负责召回、双字负责区分度，无需词典也无需训练。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

# CJK 统一表意文字（含扩展 A）
_CJK = r"一-鿿㐀-䶿"
_CJK_RUN = re.compile(f"[{_CJK}]+")
_LATIN_RUN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._\-]*")

# Markdown 语法噪声，检索前剥掉
_MD_NOISE = re.compile(r"[#*`>\[\]()|_~]+")

# 极短停用词：这些字在中文语料里几乎无区分度，留着只会稀释权重
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "有", "我", "你", "他", "她", "它",
    "们", "这", "那", "个", "吗", "呢", "吧", "啊", "呀", "么", "不", "就", "都",
    "也", "还", "很", "要", "会", "能", "可", "以", "及", "为", "对", "从", "到",
    "the", "a", "an", "of", "to", "is", "are", "and", "or", "in", "on", "for",
}


def normalize(text: str) -> str:
    """全角转半角 + 小写 + 去 markdown 噪声，保证同一内容的不同写法能对上。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _MD_NOISE.sub(" ", text)
    return text.lower()


def tokenize(text: str) -> List[str]:
    """把文本切成检索用的 token 列表（保留重复，由调用方决定是否计频）。"""
    normalized = normalize(text)
    if not normalized:
        return []

    tokens: List[str] = []

    for run in _CJK_RUN.findall(normalized):
        # 单字
        for ch in run:
            if ch not in _STOPWORDS:
                tokens.append(ch)
        # 相邻双字：这是区分度的主要来源
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _STOPWORDS:
                tokens.append(bigram)

    for run in _LATIN_RUN.findall(normalized):
        if len(run) == 1 and not run.isdigit():
            continue  # 单个 ASCII 字母没有区分度
        tokens.append(run)
        # 带连字符/点的编号再拆一层，让 "sku-bt500" 也能被 "bt500" 命中
        for part in re.split(r"[._\-]+", run):
            if len(part) > 1 and part != run:
                tokens.append(part)

    return tokens


def token_set(text: str) -> set:
    """去重后的 token 集合，用于覆盖率类指标。"""
    return set(tokenize(text))


def char_ngrams(text: str, n: int = 2) -> Iterable[str]:
    """纯字符 n-gram（不分词），用于「查询词是否原样出现在片段中」这类判断。"""
    normalized = normalize(text)
    for i in range(len(normalized) - n + 1):
        yield normalized[i : i + n]
