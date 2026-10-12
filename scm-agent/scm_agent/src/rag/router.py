"""
双通道路由：一个问题该走制度文档、图谱关联，还是两条都走。

三类
----
    DOC        资料性提问（制度 / 流程 / 标准 / 规格）—— 只查文档语料
    RELATION   关系性提问（替代 / 比价 / 多仓库存）—— 走图谱多跳，文档照旧召回
    BOTH       两类词都出现 —— 两条通道都全权重参与融合

路由的后果有三处，第一处才是实打实的：**要不要把图谱通道接进来**。
`DOC` 与另外两类的差别在于图事实根本不进候选集；`RELATION` 与 `BOTH`
之间只差文档候选的 RRF 权重（0.5 / 1.0），不改变候选集合本身 ——
名次由重排的绝对分重定，权重只影响 tie-break 与融合排序的先后。第三处是
**引用配额**：通道含图谱时，引用清单里必须留一条图事实
（`rag/index.py::_keep_relation_evidence`），免得关系问题被制度条款答满、
多跳查出来的事实一条都进不了答案。
所以词表策略是：**关系词宁滥勿缺**，多认了顶多影响一条引用位的构成；
漏认了则是「明明有图事实却不用」，那是功能性缺失。

安全侧
------
两类词一个都没中 → 判 DOC。默认走文档通道永远不会「凭空多出事实」，
图事实只在明确出现关系词时才进候选，这条保守默认是整个路由的安全阀。

与同义词表的关系
----------------
路由词表是**字面**匹配（不做分词）：改用「磕碰 -> 破损」这类同义词扩展
在检索层完成，路由在扩展**后**的文本上判类 —— 与 `confidence.ask_coverage`
同一原则：既然系统承认两词等价，路由也该认。误判仍可通过在 SYNONYMS
里补一条词兜底，不用改算法。
"""

from __future__ import annotations

from enum import Enum


class Channel(str, Enum):
    """检索通道。值用大写，与 `models/scm_models.py::MaterialQA.channel` 一致。"""

    DOC = "DOC"
    RELATION = "RELATION"
    BOTH = "BOTH"


#: 关系词：出现即把图谱通道接进来（见模块文档「宁滥勿缺」）。
RELATION_WORDS = (
    "替代", "替换", "代用", "断料", "缺料",
    "比价", "哪家", "报价", "便宜", "价格",
    "调拨", "有货", "哪个仓", "那个仓", "哪仓",
    "安全库存", "库存", "仓库", "备货",
    "交期", "准时率", "不良率", "履约", "评分", "排名",
)

#: 资料词：与关系词同现时判 BOTH（两条通道都要）。
DOC_WORDS = (
    "制度", "规定", "流程", "标准", "办法", "规范", "原则",
    "审批", "要求", "怎么", "如何", "是什么", "什么是", "多少",
    "期限", "处罚", "条件",
)

#: 给时间线/页面用的中文名
LABELS = {
    Channel.DOC: "制度文档",
    Channel.RELATION: "图谱关联",
    Channel.BOTH: "文档 + 图谱双通道",
}


def classify(text: str) -> Channel:
    """按字面词表给问题分通道；两类都不中时判 DOC（安全侧）。"""
    text = text or ""
    relation = any(w in text for w in RELATION_WORDS)
    document = any(w in text for w in DOC_WORDS)

    if relation and document:
        return Channel.BOTH
    if relation:
        return Channel.RELATION
    return Channel.DOC
