"""
Store 提供者：让 StoreClient 既能在 LangGraph 运行时内工作，也能在纯离线场景下工作。

背景
----
`langgraph.config.get_store()` 只在图执行上下文内可用；脱离运行时（例如 `python demo.py`
里导入图、或在 pytest 中直接调用工具函数）会抛：

    RuntimeError: Called get_config outside of a runnable context

因此 StoreClient 不能在构造时就去取 store，必须懒加载并带兜底。
"""

from __future__ import annotations

from typing import Optional

from langgraph.config import get_store
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from utils.agent_logger import get_logger

logger = get_logger("store_provider")

# 离线兜底 store：进程级单例。
# demo/quickstart 会把这个同一个实例传给 graph.compile(store=...)，保证读写不分裂。
_fallback_store: Optional[BaseStore] = None


def get_fallback_store() -> BaseStore:
    """获取离线兜底 store（进程级单例）。"""
    global _fallback_store
    if _fallback_store is None:
        _fallback_store = InMemoryStore()
        logger.debug("已创建离线 InMemoryStore 兜底实例")
    return _fallback_store


def reset_fallback_store() -> None:
    """重置兜底 store（主要给测试用，保证用例之间互不污染）。"""
    global _fallback_store
    _fallback_store = None


def resolve_store() -> BaseStore:
    """
    解析当前可用的 store。

    优先级：
    1. 图运行上下文内的真实 store（langgraph dev / 平台运行时注入，生产走这条）
    2. 进程级 InMemoryStore 兜底（离线 demo / 单元测试走这条）
    """
    try:
        store = get_store()
        if store is not None:
            return store
    except RuntimeError:
        # 脱离运行时，正常回落到兜底 store
        pass
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning(f"获取运行时 store 失败，回落兜底 store: {e}")

    return get_fallback_store()
