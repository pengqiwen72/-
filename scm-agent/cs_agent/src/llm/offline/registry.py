"""
离线合成器注册表。

每个合成器负责「把提示词解析成视图 → 合成目标模型需要的 dict」。
`build_payload()` 是离线模型的统一出口：识别目标模型 → 调用合成器 → **强制 schema 校验**。

强制校验是关键：它保证离线模型永远不会吐出调用方 `model_validate_json()` 会拒绝的内容，
最坏情况也只是逐级降级（合成结果 → 模型示例实例 → 空对象），图不会崩。
"""

from __future__ import annotations

from typing import Callable, Dict, Tuple, Type

from models.cs_base_model import CsBaseModel
from utils.agent_logger import get_logger
from llm.offline.prompt_view import PromptView

logger = get_logger("offline_registry")

# 类名 -> (模型类, 合成函数)
REGISTRY: Dict[str, Tuple[Type[CsBaseModel], Callable[[PromptView], dict]]] = {}

# 类名 -> 该模型字段名集合（用于按示例 JSON 的字段签名兜底识别目标模型）
KEY_SIGNATURES: Dict[str, set] = {}


def synthesizer(model_cls: Type[CsBaseModel]):
    """把一个合成函数注册到指定模型上。"""

    def decorator(fn: Callable[[PromptView], dict]):
        REGISTRY[model_cls.__name__] = (model_cls, fn)
        KEY_SIGNATURES[model_cls.__name__] = set(model_cls.model_fields)
        return fn

    return decorator


def build_payload(prompt: str) -> str:
    """离线模型的回答：解析提示词，合成并校验出目标模型的 JSON 字符串。"""
    view = PromptView(prompt)
    target = view.detect_target_model(KEY_SIGNATURES)

    if not target or target not in REGISTRY:
        logger.warning(
            f"离线模型无法识别目标结构（已注册: {sorted(REGISTRY)}），返回空对象"
        )
        return "{}"

    model_cls, synthesize = REGISTRY[target]

    try:
        data = synthesize(view)
        # 关键：合成结果必须通过 schema 校验才返回
        return model_cls.model_validate(data).model_dump_json()
    except Exception as e:
        logger.warning(f"离线合成 {target} 失败：{e}，回退到示例实例")
        try:
            return model_cls.get_example_instance().model_dump_json()
        except Exception:
            return "{}"
