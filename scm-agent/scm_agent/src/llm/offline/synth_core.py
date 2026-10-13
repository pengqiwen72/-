"""
核心模型的离线合成器：意图识别、闲聊应答。

合成器的共同约定：**数据驱动**，不是返回固定常量。这样才能保证离线跑出来的
结果和输入真的相关——demo 时可解释，也经得起面试官追问「这不就是个假数据吗」。
"""

from __future__ import annotations

from typing import Optional

from models.scm_models import Intent, JustTalkOutput
from llm.offline.prompt_view import PromptView
from llm.offline.registry import synthesizer
from llm.offline.lexicons import INTENT_KEYWORDS, score_intent_by_keywords
from utils.tokenize import tokenize

# 兜底意图 id：没有任何意图拿到证据时分给它
NO_CLEAR_INTENTION = "no_clear_intention"


def _longest_common_substring_len(a: str, b: str) -> int:
    """最长公共子串长度——用来衡量任务输入与意图描述的「语义接近度」。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


@synthesizer(Intent)
def synthesize_intent(view: PromptView) -> dict:
    """离线意图识别：关键词打分 + 与意图描述的文本相似度兜底。

    与线上走的 `llm_intent_detect()` 是同一份提示词，只是这里用确定性算法替代模型。
    """
    # 意图候选列表：形如 [{"intent_id": ..., "intent_description": ...}, ...]
    candidates = view.find_list("intent_id", "intent_description") or []

    # 任务输入：提示词最后一个数据块
    task_input = view.last_block()

    if not candidates:
        return {"intent_id": "", "intent_description": ""}

    best_intent = None
    best_score = float("-inf")

    for cand in candidates:
        intent_id = cand.get("intent_id", "")
        description = cand.get("intent_description", "")

        # 1) 关键词命中（权重最高，业务词表最可靠）
        keyword_score = score_intent_by_keywords(task_input, intent_id) * 3.0

        # 2) 与意图描述的 token 重叠（兜底，处理词表没覆盖的说法）
        #    按 token 长度加权：tokenizer 同时产单字与双字，双字（"收货"）比
        #    单字（"货"）更能说明意图。加权后单字命中权重为 0 —— 免得「货」
        #    「仓」这类到处出现的字靠数量把某个意图顶上去。
        desc_tokens = set(tokenize(description))
        input_tokens = set(tokenize(task_input))
        overlap = sum(len(tok) - 1 for tok in (desc_tokens & input_tokens))

        # 3) 最长公共子串（处理「复制粘贴式」的显式指令）
        lcs = _longest_common_substring_len(task_input, description)

        score = keyword_score + overlap + lcs * 0.5

        # 4) 平局倾向兜底意图：谁都没拿到证据时，「没有明确意图」比
        #    「碰巧排在候选列表第一位」更接近正确答案。
        #    没有这一条，一句「你好呀」会因为所有分都是 0 而落到 chat_suggestion。
        if intent_id == NO_CLEAR_INTENTION:
            score += 0.01

        if score > best_score:
            best_score = score
            best_intent = cand

    if best_intent is None:  # pragma: no cover - candidates 非空时不会走到
        best_intent = candidates[-1]

    return {
        "intent_id": best_intent.get("intent_id", ""),
        "intent_description": best_intent.get("intent_description", ""),
    }


@synthesizer(JustTalkOutput)
def synthesize_just_talk(view: PromptView) -> dict:
    """离线闲聊应答：说明自身能力边界，并给出可用的演示入口。"""
    task_input = view.last_block()

    capability = (
        "我是供应链采购仓储智能助手，可以帮你处理这些事：\n"
        "1. 物料与制度问答——基于制度知识库与物料关系回答规格、替代料、验收标准类问题，回答带出处；\n"
        "2. 采购申请——核对采购要素与审批策略，大额申请走人工审批后下单；\n"
        "3. 收货验收——登记到货与不良，不良率超阈值时转人工复核；\n"
        "4. 库存管理——查看多仓分布、安全库存预警与可支撑天数；\n"
        "5. 供应商管理——比价与履约评分排名。"
    )

    if task_input:
        answer = f"你问到的是「{task_input}」。\n{capability}"
    else:
        answer = capability

    return {"just_talk_output": answer}
