"""
LLM 提供者：统一产出真实模型或离线模型，调用方无感知。

为什么是 BaseChatModel 子类而不是简单包装函数
---------------------------------------------
`cs_graph.py` 通过 `.with_config({"callbacks": [langfuse_handler]})` 挂载链路追踪。
只有真正的 Runnable 才会传播回调——鸭子类型的假对象会静默丢掉追踪，
导致离线与线上的可观测性不一致。继承 BaseChatModel 顺带还拿到了
`.stream()` / `.with_structured_output()` / `.bind_tools()`。

模式切换
--------
LLM_MODE=offline（默认）→ OfflineChatModel，零配置可跑
LLM_MODE=live            → init_chat_model()，需要 MODEL_PROVIDER / MODEL_NAME / API_KEY
即使填了 live 但缺关键配置，也会自动回落离线并告警，不会让 demo 直接崩。
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from utils.agent_logger import get_logger
from utils.env_loader import load_agent_env

load_agent_env()  # 统一入口，别裸调 load_dotenv()（见 utils/env_loader.py）

logger = get_logger("llm_provider")


class OfflineChatModel(BaseChatModel):
    """
    离线确定性模型。

    它不「生成」文本，而是解析提示词里内嵌的目标 schema 与输入数据，合成出
    schema 合法的结构化 JSON。这样既能零配置跑通全链路，又保证输出与真实
    模型走同一套解析路径（调用方的 `model_validate_json` 完全不用改）。
    """

    model_name: str = Field(default="offline-stub")
    strict: bool = Field(default=True)

    @property
    def _llm_type(self) -> str:
        return "offline-stub"

    @property
    def _identifying_params(self) -> dict:
        return {"model_name": self.model_name, "strict": self.strict}

    @staticmethod
    def _extract_prompt(messages: List[BaseMessage]) -> str:
        """调用方都是 `llm.invoke(prompt_str)`，取最后一条消息的文本即可。"""
        if not messages:
            return ""
        content = messages[-1].content
        if isinstance(content, str):
            return content
        # 多模态/结构化内容：拼接其中的文本片段
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # 延迟导入：让 registry 的注册 side-effect 在首次调用前完成
        from llm.offline.registry import build_payload

        prompt = self._extract_prompt(messages)
        payload = build_payload(prompt)

        logger.debug(f"离线模型合成结果（{len(payload)} 字符）")

        message = AIMessage(content=payload)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_chat_model(**_kwargs: Any) -> BaseChatModel:
    """按 LLM_MODE 产出聊天模型实例。"""
    mode = (os.getenv("LLM_MODE") or "offline").strip().lower()

    if mode not in {"live", "real"}:
        logger.debug("LLM_MODE=%s，使用离线确定性模型", mode or "offline")
        return OfflineChatModel()

    provider = os.getenv("MODEL_PROVIDER")
    model = os.getenv("MODEL_NAME")

    if not provider or not model:
        logger.warning(
            "LLM_MODE=live 但未配置 MODEL_PROVIDER / MODEL_NAME，自动回落到离线模型"
        )
        return OfflineChatModel()

    try:
        from langchain.chat_models import init_chat_model

        logger.info(f"使用真实模型：{provider}/{model}")
        return init_chat_model(
            model_provider=provider,
            model=model,
            base_url=os.getenv("BASE_URL"),
            api_key=os.getenv("API_KEY"),
        )
    except Exception as e:
        logger.warning(f"初始化真实模型失败（{e}），回落到离线模型")
        return OfflineChatModel()


def is_offline() -> bool:
    """当前是否为离线模式（demo/README 展示用）。"""
    return isinstance(get_chat_model(), OfflineChatModel)
