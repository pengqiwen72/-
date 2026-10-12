"""
文本小工具：出处的展示路径拼接。

这里只有一件事，但它是问答链路的**可信度**所在：出处必须能被人核对。
制度语料里尤其明显——几份文档都有名为「审批规则」的小节，所以不能只写
章节名而不写标题，否则「依据」两个字就是空的。
"""

from __future__ import annotations


def citation_path(title: str, section: str) -> str:
    """出处的展示路径：「文档标题 > 章节」，标题与章节重复时只留一份。

    标题必须出现在出处里。制度语料里尤其明显：几份文档都有名为「审批规则」
    的小节，只写章节名的话，出处完全无法核对——这条规则到底是《采购管理办法》
    的还是《供应商管理制度》的？「依据可核对」正是出处的全部意义。

    反过来，章节路径常常已经含了标题（「采购管理办法 > 审批规则」），
    再拼一次就变成「采购管理办法 > 采购管理办法 > 审批规则」。
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
