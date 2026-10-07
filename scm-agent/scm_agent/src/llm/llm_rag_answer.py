"""
商品咨询回答生成（在线模式）。

这是「模型只负责转述」里模型那一半：它拿到的是**已经检索好、原文照抄**的事实单，
任务只是把事实单改写成一句口语化的客服回复。

真正的约束不在提示词里，而在调用方 `rag.answer.verify_no_new_numbers`：
模型输出之后会逐个数字比对，任何事实单里没有的数字都会被判定为编造，
输出被丢弃、回退为确定性转述。所以这个函数**允许**模型说错话——
说错会被机械地拦下来，而不是靠提示词祈祷它别说错。

离线模式不会走到这里（`llm_setting.is_offline_model()` 拦住），
因为离线模型不做自然语言生成，它的产物是结构化 JSON。
"""

from __future__ import annotations

from rag.answer import build_prompt
from utils.agent_logger import get_logger

logger = get_logger("llm_rag_answer")


def llm_rag_answer(question: str, facts: str) -> str:
    """把事实单转述成客服回复。返回纯文本（非 JSON）。

    签名刻意与 `rag.index.ProductKnowledgeBase.ask(generator=...)` 约定的
    `(question, facts) -> str` 对齐，可以直接作为生成器传入。
    """
    from llm.llm_setting import chat_model as llm

    response = llm.invoke(build_prompt(question, facts))
    content = getattr(response, "content", "") or ""
    if not isinstance(content, str):  # 多模态内容块：拼出文本部分
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return content.strip()
