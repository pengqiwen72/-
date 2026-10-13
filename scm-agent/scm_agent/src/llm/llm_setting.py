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


def is_offline_model() -> bool:
    """当前生效的模型是不是离线模型。

    判断的是**实际实例**而不是 `LLM_MODE` 环境变量：填了 live 但缺
    MODEL_PROVIDER / MODEL_NAME 时会自动回落离线，此时环境变量说 live、
    真实跑的是离线，只看环境变量会得出相反的结论。也不走
    `llm_provider.is_offline()`——那个每次调用都重新构造一遍模型。
    """
    return isinstance(chat_model, OfflineChatModel)


__all__ = [
    "chat_model",
    "reset_chat_model",
    "is_offline_model",
    "OfflineChatModel",
    "get_chat_model",
    "is_offline",
]
