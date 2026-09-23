"""
进程内驱动 LangGraph 图的运行器。

它是「离线零配置」的核心：不启动任何 HTTP 服务，直接编译图、喂输入、消费事件流，
并在 `interrupt()` 处暂停等待人工决策，然后**用同一个 thread_id** 恢复。

与平台模式的关系
----------------
生产走 `langgraph dev`，由平台负责线程管理；这里用 `InMemorySaver` 顶替，
让 demo / pytest 无需任何外部依赖。

⚠️ 一个容易踩的坑：恢复时必须重发 `configurable.user_id` / `external_id`。
这些值**不会**从检查点恢复，而下游节点（如 update 节点）会直接读它们，
漏发会 KeyError。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("demo_runner")

# 默认演示身份：客服坐席 CS001 服务客户 wx_ext_001
DEFAULT_USER_ID = "CS001"
DEFAULT_EXTERNAL_ID = "wx_ext_001"

# HITL 人工反馈协议的字段名，与各子图 `HumanFeedback.FIELD_NAME` 保持一致
HITL_FIELD_NAME = "confirmed"


def new_thread_id(prefix: str = "demo") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class RunResult:
    """一次运行（到暂停或结束）的结果。"""

    thread_id: str
    events: list[dict] = field(default_factory=list)     # 进度事件（custom 通道）
    updates: list[dict] = field(default_factory=list)    # 节点更新（updates 通道）
    interrupts: list[Any] = field(default_factory=list)  # 待人工处理的中断
    state: dict = field(default_factory=dict)            # 最终状态快照
    error: Optional[str] = None

    @property
    def interrupted(self) -> bool:
        return bool(self.interrupts)

    @property
    def task_result(self):
        return (self.state or {}).get("task_result")

    @property
    def node_results(self) -> list:
        return (self.state or {}).get("node_result") or []


def build_demo_graph(*, checkpointer=None, store=None, load_seed: Optional[bool] = None):
    """编译一个可在进程内运行的图（离线 store + 内存检查点）。

    默认会先把 `data/seed/*.json` 灌进 store —— 否则每个读数据的节点都拿到空值，
    图「跑得通」但看不到任何业务产出。`LOAD_SEED=false` 可关掉。
    """
    from graphs.cs_graph.cs_graph import build_graph

    resolved_store = store or get_fallback_store()

    if load_seed is None:
        load_seed = (os.getenv("LOAD_SEED", "true") or "").strip().lower() not in {"0", "false", "no", "off"}
    if load_seed:
        from store.seed_loader import load_seed_data

        load_seed_data(resolved_store)

    return build_graph(
        checkpointer=checkpointer or InMemorySaver(),
        store=resolved_store,
    )


class DemoRunner:
    """驱动图跑完一个业务场景，含 HITL 中断/恢复。"""

    def __init__(
        self,
        graph=None,
        *,
        user_id: str = DEFAULT_USER_ID,
        external_id: str = DEFAULT_EXTERNAL_ID,
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        self.graph = graph or build_demo_graph()
        self.user_id = user_id
        self.external_id = external_id
        self.on_event = on_event

    # ---------- 配置 ----------

    def thread_config(self, thread_id: str) -> dict:
        """构造线程配置。

        恢复时必须复用同一份 —— 且必须带上 user_id / external_id，
        因为它们不从检查点恢复，缺失会让下游节点 KeyError。
        """
        return {
            "configurable": {
                "thread_id": thread_id,
                "user_id": self.user_id,
                "external_id": self.external_id,
            }
        }

    # ---------- 执行 ----------

    def _consume(self, payload: Any, config: dict) -> RunResult:
        """喂输入并消费事件流，直到结束或停在 interrupt。"""
        thread_id = config["configurable"]["thread_id"]
        result = RunResult(thread_id=thread_id)

        try:
            stream = self.graph.stream(
                payload,
                config,
                stream_mode=["updates", "custom"],
                subgraphs=True,
            )

            for chunk in stream:
                # subgraphs=True 时事件是 (namespace, mode, data) 三元组
                if isinstance(chunk, tuple) and len(chunk) == 3:
                    _namespace, mode, data = chunk
                elif isinstance(chunk, tuple) and len(chunk) == 2:
                    mode, data = chunk
                else:  # pragma: no cover - 兼容不同 langgraph 版本的形状
                    mode, data = "updates", chunk

                if mode == "custom":
                    event = dict(data) if isinstance(data, dict) else {"raw": data}
                    result.events.append(event)
                    if self.on_event:
                        self.on_event(event)

                elif mode == "updates":
                    if isinstance(data, dict):
                        result.updates.append(data)

        except Exception as e:
            logger.error(f"图执行失败：{e}", exc_info=True)
            result.error = str(e)

        # 取状态快照（含中断信息）
        snapshot = self.graph.get_state(config)
        result.state = dict(snapshot.values or {})
        result.interrupts = list(snapshot.interrupts or [])

        return result

    def start(self, task_input: str, *, thread_id: Optional[str] = None) -> RunResult:
        """发起一次任务。"""
        thread_id = thread_id or new_thread_id()
        config = self.thread_config(thread_id)
        return self._consume({"task_input": task_input}, config)

    def resume(self, thread_id: str, decision: Any = "ok") -> RunResult:
        """
        恢复被中断的任务。

        注意用同一 thread_id 与同一份 configurable —— 见模块顶部说明。

        `decision` 可以有两种写法，二者等价：
          * 标量 `"ok"` / `"discard"` / `"recreate"` —— 自动包成 HITL 协议要求的
            `{"confirmed": <decision>}`，这是日常用法；
          * 完整字典 `{"confirmed": "ok"}` —— 原样透传，给需要自定义协议的节点用。

        ⚠️ 这里刻意做了归一化：HITL 的 resume 值会被 `interrupt()` 原样返回，
        多包一层就会落进「指令异常」兜底分支，而报错信息完全看不出是包了两层。
        """
        if isinstance(decision, dict):
            payload = decision
        else:
            payload = {HITL_FIELD_NAME: decision}

        config = self.thread_config(thread_id)
        return self._consume(Command(resume=payload), config)

    def get_state(self, thread_id: str) -> dict:
        """读取线程当前状态（不推进执行）。"""
        return dict(self.graph.get_state(self.thread_config(thread_id)).values or {})
