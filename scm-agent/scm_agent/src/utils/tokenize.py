"""
轻量中文友好分词，供离线合成器与 RAG 检索共用。

设计取舍
--------
中文检索常见做法是上 jieba 分词，但那会引入额外依赖，且对「冷轧钢板」「安全库存」
这类领域词反而容易切碎。这里改用**字符 n-gram**：

  * CJK 连续串 -> 单字 + 相邻双字（"冷轧钢板" -> 冷, 轧, 钢, 板, 冷轧, 轧钢, 钢板）
  * 拉丁/数字串 -> 整串 + 子串（"M100023" -> m100023, m100, 100023）

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

# 极短停用词：这些字在中文语料里几乎无区分度，留着只会稀释权重。
# 双字组也收 —— 这里收的是**疑问壳**：「有没有」几乎出现在每个口语化提问里，
# 制度语料的 FAQ 问句里也到处都是。不收的话，「问的东西资料里压根没有」的问题
# 会靠这两个字拿到非零的实词覆盖率，`rag/confidence.py` 的判负线就是空的
# （实测：去掉之前，「公司制度里有没有无人机送货的规定？」的诉求词覆盖率
# 0.13，全是「有没有」贡献的）。
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "有", "我", "你", "他", "她", "它",
    "们", "这", "那", "个", "吗", "呢", "吧", "啊", "呀", "么", "不", "就", "都",
    "也", "还", "很", "要", "会", "能", "可", "以", "及", "为", "对", "从", "到",
    "没", "有没", "没有", "有无",
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


def content_terms(text: str) -> List[str]:
    """**实词**：长度 >= 2、且双字组不跨停用字的 token（去重，保持出现顺序）。

    检索与覆盖率是两把尺子，对同一批 token 的容忍度不一样：

      * **检索（BM25）** 不怕噪声。双字组跨词边界产出的记号（「的规」←
        《…办法》**的规**则、「度里」← 制**度里**）在所有文档里都出现，idf
        近似为零，加权求和时自然被压掉 —— 这是 tf-idf 的看家本领。
      * **覆盖率**是不加权的比例：命中一个凑巧的「的规」就多一分，分母还
        被虚词撑大。实测踩过：「公司制度里有没有无人机送货的规定？」的诉求词
        覆盖率停在 0.09，全部来自「的规」—— 于是「资料里没有」的问题差一点
        被硬答出来（见 `rag/confidence.py` 的判负线）。

    所以「实词」的口径单独放在这里：双字组只要含一个停用字就不算实词。
    代价是「可用」「有货」这类含停用字的真词也会被排除 —— 判据侧可以接受
    （它们的存在与否不影响「问的东西资料里有没有」），检索侧不换尺子。
    """
    terms: List[str] = []
    for t in dict.fromkeys(tokenize(text)):
        if len(t) < 2:
            continue
        if len(t) == 2 and (t[0] in _STOPWORDS or t[1] in _STOPWORDS):
            continue
        terms.append(t)
    return terms


def char_ngrams(text: str, n: int = 2) -> Iterable[str]:
    """纯字符 n-gram（不分词），用于「查询词是否原样出现在片段中」这类判断。"""
    normalized = normalize(text)
    for i in range(len(normalized) - n + 1):
        yield normalized[i : i + n]
