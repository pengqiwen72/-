"""
模型入口。

所有 `llm_*.py` 都通过 `from llm.llm_setting import chat_model as llm` 取模型，
因此这里是真实模型与离线模型的**唯一切换点**——下游 7 个调用方零改动。

历史版本在 import 时直接 `init_chat_model(...)`，未配置 API Key 会抛异常，
导致整个包无法离线导入；现在由 `get_chat_model()` 负责兜底。
"""

from __future__ import annotations

import llm.offline  # noqa: F401  —— 触发离线合成器注册（side-effect）

from llm.llm_provider import OfflineChatModel, get_chat_model, is_offline

chat_model = get_chat_model()


def reset_chat_model():
    """重新解析模型（环境变量变更或测试时使用）。"""
    global chat_model
    chat_model = get_chat_model()
    return chat_model


__all__ = ["chat_model", "reset_chat_model", "OfflineChatModel", "get_chat_model", "is_offline"]
