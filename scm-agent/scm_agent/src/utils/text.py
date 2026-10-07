"""
文本切分小工具。

抽到 `utils` 而不是各自写一份，是因为它编码的不只是字符串处理，而是一条
**业务判断**：客户发来一大段话时，哪部分才是他真正在问的事。问答链路
（`sub_product_qa`）和话术链路（`llm/offline/synth_ops`）都要做这个判断，
两处必须给出同一个答案——否则同一条客户消息，问答链路去查破损赔付、
话术链路去查交付时限，坐席看到的两个建议互相打架。
"""

from __future__ import annotations

import re
from typing import List

#: 句末标点：中文的。！？与半角的 !?
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])\s*")

#: 判定「这是在提问」的收尾标点
_QUESTION_END = ("？", "?")


def split_sentences(text: str) -> List[str]:
    """按句末标点切句，丢掉空白与空句。"""
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def last_question_clause(text: str) -> str:
    """取出客户消息**末尾连续的疑问句**，作为「客户在问什么」。

    为什么既不是整段、也不是最后一句
    ---------------------------------
    客户的消息常常是复合的：

        「好的，那就下单这个阅读灯。对了，如果收到发现灯臂有磕碰，
          你们是怎么处理的？多久能给我答复？」

    * **整段**去检索，「下单」「阅读灯」这些词会把查询稀释掉，
      命中的条款跟着跑偏；
    * **最后一句**（「多久能给我答复？」）又太短——「多久」「答复」
      在知识库里到处都是，同样对不准；
    * 真正要回答的是**末尾那一串疑问句**：前一句交代场景，后一句追问时限，
      合起来才是完整诉求。

    所以规则是：从末尾往前扫，收集连续的疑问句；一句疑问句都没有时
    （例如「我想退货」这类陈述式诉求），退回最后一句。
    """
    sentences = split_sentences(text)
    if not sentences:
        return (text or "").strip()

    collected: List[str] = []
    for sent in reversed(sentences):
        if not sent.endswith(_QUESTION_END):
            break
        collected.append(sent)

    if not collected:
        return sentences[-1]

    return "".join(reversed(collected))


def citation_path(title: str, section: str) -> str:
    """出处的展示路径：「文档标题 > 章节」，标题与章节重复时只留一份。

    标题必须出现在出处里。商品页尤其明显：六个 SKU 都有名为「发货与运费」
    的小节，只写章节名的话，出处完全无法核对——这条运费规则到底是保温杯的
    还是磨豆机的？而客服场景里「依据可核对」正是出处的全部意义。

    反过来，政策文档的章节路径常常已经含了标题（「售后与退换货总则 > 破损包赔」），
    再拼一次就变成「售后与退换货总则 > 售后与退换货总则 > 破损包赔」。
    """
    title = (title or "").strip()
    section = (section or "").strip()
    if not title:
        return section
    if not section:
        return title
    if section == title or section.startswith(f"{title} >"):
        return section
    return f"{title} > {section}"
