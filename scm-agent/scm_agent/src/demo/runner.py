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
from typing import Any, Callable, Iterator, Optional, Tuple

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from observability.progress import node_event
from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("demo_runner")

# 默认演示身份：采购员 OP001（提报人；采购申请单号、审批记录都落在他身上）
# external_id 保留在配置里不删：ConfigurableField 是两个键的契约，
# 缺一个下游节点就 KeyError（历史踩过），供应链域暂时不用它，传空串。
DEFAULT_USER_ID = "OP001"
DEFAULT_EXTERNAL_ID = ""

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


def _plain(value: Any) -> Any:
    """把状态里的对象转成可 JSON 序列化的结构。

    状态里装的是 pydantic 模型（TaskResult / OrderQueryResult / WeeklyReport …），
    直接丢给 JSONResponse 会 TypeError。这里统一走 `model_dump(mode="json")`，
    而不是逐个字段手写 —— 模型加字段时不会漏。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return str(value)


def _serialize_interrupt(item: Any) -> dict:
    """中断对象（`Interrupt`）转成给前端用的结构。

    `value` 就是节点传给 `interrupt()` 的那份载荷（审批卡片要展示的内容），
    `id` 用于前端做「同一次中断不要重复提交」的去重。
    """
    return {
        "id": str(getattr(item, "id", "") or ""),
        "value": _plain(getattr(item, "value", item)),
    }


def hitl_payload(decision: Any) -> Any:
    """归一化人工审批的决策值。

    HITL 协议要求 `{"confirmed": "ok"|"discard"|"recreate"}`，但调用方（命令行、
    HTTP、测试）更想直接传 `"ok"`。归一化只做在这一处：

    ⚠️ 这里刻意做了兼容而不是要求调用方自己包 —— `interrupt()` 会把 resume 值
    **原样**返回给节点，多包一层就会落进「指令异常」兜底分支，而报错信息完全
    看不出是包了两层。把归一化收敛在一处，就不存在「某个入口忘了包/多包了」。
    """
    return decision if isinstance(decision, dict) else {HITL_FIELD_NAME: decision}


def build_demo_graph(*, checkpointer=None, store=None, load_seed: Optional[bool] = None):
    """编译一个可在进程内运行的图（离线 store + 内存检查点）。

    默认会先把 `data/seed/*.json` 灌进 store —— 否则每个读数据的节点都拿到空值，
    图「跑得通」但看不到任何业务产出。`LOAD_SEED=false` 可关掉。
    """
    from graphs.scm_graph.scm_graph import build_graph

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
            },
            # Langfuse 的会话归组：同一线程的多次调用（发起 + 若干次审批恢复）
            # 在图里是好几段独立的 trace，没有这几个键在后台就是一堆散点。
            # 它们只在启用追踪时被读取，不启用时是无害的普通元数据。
            "metadata": {
                "langfuse_session_id": thread_id,
                "langfuse_user_id": self.user_id,
                "langfuse_tags": ["scm-agent", "demo"],
            },
        }

    # ---------- 执行 ----------

    def _iter(self, payload: Any, config: dict) -> Iterator[Tuple[str, dict]]:
        """消费图的事件流，逐条吐出 `(kind, data)`。

        kind 取 `"progress"`（进度事件）或 `"update"`（节点更新）。
        不在这里取最终快照 —— 快照要等流真正结束才有意义，交给调用方。

        进度事件有两个来源：节点里手写的 `emit()`（custom 通道），以及**节点跑完
        但什么都没 emit** 时由 `node_event()` 兜底生成的一条（见 `NODE_EVENTS`）。
        没有兜底的话，没写 emit 的那几条链路在界面上是空白的 —— 点了没反应，
        然后审批卡片突然弹出来。
        """
        stream = self.graph.stream(
            payload,
            config,
            stream_mode=["updates", "custom"],
            subgraphs=True,
        )

        # 上一个节点自己 emit 过没有。custom 事件先于该节点的 update 到达，
        # 所以读到 update 时这个标记反映的正是「刚跑完的那个节点」。
        emitted = False

        for chunk in stream:
            # subgraphs=True 时事件是 (namespace, mode, data) 三元组
            if isinstance(chunk, tuple) and len(chunk) == 3:
                _namespace, mode, data = chunk
            elif isinstance(chunk, tuple) and len(chunk) == 2:
                mode, data = chunk
            else:  # pragma: no cover - 兼容不同 langgraph 版本的形状
                mode, data = "updates", chunk

            if mode == "custom":
                emitted = True
                yield "progress", dict(data) if isinstance(data, dict) else {"raw": data}
            elif mode == "updates" and isinstance(data, dict):
                if not emitted:
                    for node_name in data:
                        event = node_event(node_name)
                        if event:
                            yield "progress", event
                emitted = False
                yield "update", data

    def _snapshot(self, config: dict, result: RunResult) -> RunResult:
        snapshot = self.graph.get_state(config)
        result.state = dict(snapshot.values or {})
        result.interrupts = list(snapshot.interrupts or [])
        return result

    def _consume(self, payload: Any, config: dict) -> RunResult:
        """喂输入并消费事件流，直到结束或停在 interrupt。"""
        thread_id = config["configurable"]["thread_id"]
        result = RunResult(thread_id=thread_id)

        try:
            for kind, data in self._iter(payload, config):
                if kind == "progress":
                    result.events.append(data)
                    if self.on_event:
                        self.on_event(data)
                else:
                    result.updates.append(data)
        except Exception as e:
            logger.error(f"图执行失败：{e}", exc_info=True)
            result.error = str(e)

        return self._snapshot(config, result)

    def stream(self, payload: Any, config: dict) -> Iterator[dict]:
        """逐条吐出事件，供 SSE 这类「边跑边推」的场景使用。

        与 `_consume` 共用同一套消费逻辑，区别只在**不做缓冲**：
        要是一边跑一边攒再一次性返回，前端的进度条要等到图跑完才动，
        流式推送就白做了。

        `type` 取 `"progress"` / `"update"`，最后一条是终态事件；
        序列以 `"done"` / `"interrupt"` / `"error"` 三者之一收尾。

        ⚠️ 所有事件的字段都在**顶层**（`{"type": "progress", "stage": ..., "label": ...}`），
        不再套一层 `data`。曾经 progress 是 `{"type", "data": {...}}`、终态却是平的，
        两种形状混在一起：SSE 那边直接转发，前端读 `data.label` 就全是 undefined ——
        帧照样在推，只看「有没有事件」的测试根本发现不了。
        """
        thread_id = config["configurable"]["thread_id"]
        try:
            for kind, data in self._iter(payload, config):
                yield {"type": kind, **(data if isinstance(data, dict) else {"data": data})}
        except Exception as e:
            logger.error(f"图执行失败：{e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
            return

        result = self._snapshot(config, RunResult(thread_id=thread_id))
        if result.interrupts:
            yield {"type": "interrupt", "thread_id": thread_id,
                   "interrupts": [_serialize_interrupt(i) for i in result.interrupts]}
        else:
            yield {"type": "done", "thread_id": thread_id,
                   "task_result": _plain(result.task_result)}

    def start(self, task_input: str, *, thread_id: Optional[str] = None) -> RunResult:
        """发起一次任务。"""
        thread_id = thread_id or new_thread_id()
        config = self.thread_config(thread_id)
        return self._consume({"task_input": task_input}, config)

    def resume_command(self, decision: Any = "ok") -> Command:
        """构造恢复用的 Command。

        单独抽出来，是因为流式入口（SSE）要自己调 `stream()`，
        而它同样需要这份归一化 —— 见 `hitl_payload` 的说明。
        """
        return Command(resume=hitl_payload(decision))

    def resume(self, thread_id: str, decision: Any = "ok") -> RunResult:
        """
        恢复被中断的任务。

        注意用同一 thread_id 与同一份 configurable —— 见模块顶部说明。

        `decision` 可以有两种写法，二者等价：
          * 标量 `"ok"` / `"discard"` / `"recreate"` —— 自动包成 HITL 协议要求的
            `{"confirmed": <decision>}`，这是日常用法；
          * 完整字典 `{"confirmed": "ok"}` —— 原样透传，给需要自定义协议的节点用。
        """
        config = self.thread_config(thread_id)
        return self._consume(self.resume_command(decision), config)

    def get_state(self, thread_id: str) -> dict:
        """读取线程当前状态（不推进执行）。"""
        return dict(self.graph.get_state(self.thread_config(thread_id)).values or {})
