"""
坐席工作台的后端接口：登录、流式对话、人工审批、状态回查。

为什么是**进程内**驱动图，而不是通过平台 SDK 远程调
----------------------------------------------------
生产形态是 `langgraph dev` / 平台托管线程，业务侧用 SDK 调 `:2024`（`auth/auth.py`
和 `langgraph.json` 里的 `auth` / `http` 两段就是为那条路准备的）。但这里刻意
选了另一条：在 webapp 进程里直接编译并驱动图（`demo.runner`），于是

    uvicorn src.webapp.webapp:app --port 8000

一条命令就能把整套界面跑起来，不需要 Redis / Postgres / 平台服务，也不需要
先起一个 :2024 再去连它。demo 的「零配置」承诺，在服务模式下同样成立。

代价要说清楚：checkpointer 是进程内的 `InMemorySaver`，**服务重启会话即丢**，
且多副本部署时各副本各持一份状态。要做成生产形态，替换点就是 `get_graph()`
和 `DemoRunner` —— 换成平台客户端即可，路由层不用动。

关于 SSE 的实现方式
-------------------
用同步生成器 + `StreamingResponse`，不引 sse-starlette：Starlette 会把同步迭代器
丢进线程池，而 `graph.stream()` 本身就是阻塞的，正好对上，省掉一层
「线程池 → 事件循环队列 → 异步生成器」的手工搬运。

帧格式就是最朴素的 `event:` + `data:`：

    event: progress
    data: {"stage": "retrieve_done", "label": "检索完成", ...}

事件类型见 `chat_stream` 的注释。**只有 progress 与终态会被推给浏览器**：
节点更新（updates 通道）是图内部的增量状态，既大又暴露实现细节，
推给前端只会让页面跟着图的重构一起改。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from demo.runner import DemoRunner, _plain, new_thread_id
from utils.agent_logger import get_logger
from webapp.security import AgentIdentity, create_access_token, require_agent

logger = get_logger("webapp_chat")

router = APIRouter(prefix="/cs", tags=["坐席工作台"])

# ==================== 图实例 ====================

_graph = None
_graph_lock = threading.Lock()


def get_graph():
    """进程级共享的图实例。

    必须共享：checkpointer 和 store 都挂在图实例上，每个请求各建一个图，
    等于每个请求各有一份互不可见的状态 —— 审批恢复会找不到原来的线程。
    """
    global _graph
    with _graph_lock:
        if _graph is None:
            from demo.runner import build_demo_graph

            logger.info("首次请求，正在构建图（含种子数据装载）……")
            _graph = build_demo_graph()
        return _graph


def _runner(agent: AgentIdentity) -> DemoRunner:
    return DemoRunner(
        get_graph(),
        user_id=agent.user_id,
        external_id=agent.external_id or agent.user_id,
    )


# ==================== 请求模型 ====================

class LoginRequest(BaseModel):
    user_id: str = Field(..., description="坐席账号")
    password: str = Field(..., description="登录口令")


class ChatRequest(BaseModel):
    task_input: str = Field(..., description="客户诉求原文")
    thread_id: Optional[str] = Field(default=None, description="留空则新建会话")


class ResumeRequest(BaseModel):
    thread_id: str = Field(..., description="要恢复的会话")
    decision: str = Field(default="ok", description="ok=确认 / discard=放弃 / recreate=重新生成")


# ==================== SSE ====================

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # 反代（nginx）默认会把响应缓冲起来再一次性吐给浏览器，
    # 那样 SSE 会退化成「跑完才出结果」。这个头是让 nginx 别缓冲。
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _stream(payload: Any, config: dict, runner: DemoRunner) -> Iterator[str]:
    """把 runner 的事件流翻译成 SSE 帧。"""
    thread_id = config["configurable"]["thread_id"]

    # 先发一帧「已受理」：图的第一条进度要等 load_data 跑完才出来，
    # 前端在此之前完全没反馈，看起来像点了没反应
    yield _sse("open", {
        "thread_id": thread_id,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    for item in runner.stream(payload, config):
        kind = item.get("type")
        if kind == "update":
            # 节点更新是图内部的增量状态，不推给前端（见模块说明）
            continue

        # 去掉 type：SSE 的 `event:` 行已经说明了类型，再在 data 里重复一遍
        # 只是让前端多剥一层。
        yield _sse(kind, {k: v for k, v in item.items() if k != "type"})


# ==================== 接口 ====================

@router.post("/get_token", summary="坐席登录，换取 JWT")
async def get_token(body: LoginRequest):
    """校验账号口令并签发 JWT。

    演示账号来自 `DEMO_USER_ID` / `DEMO_USER_PASSWORD`（未配置时用内置默认值，
    见 `quickstart.py --help` 与 README）。
    """
    from webapp.security import demo_credentials

    expected_user, expected_password = demo_credentials()
    if body.user_id != expected_user or body.password != expected_password:
        logger.warning(f"登录失败：user_id={body.user_id}")
        raise HTTPException(status_code=403, detail="账号或口令不正确")

    import os

    external_id = os.getenv("DEMO_USER_EXTERNAL_ID", "")
    token = create_access_token(body.user_id, external_id)
    logger.info(f"登录成功：{body.user_id}")
    return {
        "status": "ok",
        "token": token,
        "token_type": "Bearer",
        "user_id": body.user_id,
        "external_id": external_id,
    }


@router.post("/chat/stream", summary="发起一次对话（SSE）")
async def chat_stream(body: ChatRequest, agent: AgentIdentity = Depends(require_agent)):
    """流式跑一轮客服链路。

    事件类型：

    | event | 含义 |
    |---|---|
    | `open` | 已受理，带上 thread_id |
    | `progress` | 一条链路进度（stage / label / detail） |
    | `interrupt` | 停在人工审批，data.interrupts 里是审批卡片内容 |
    | `done` | 跑完，data.task_result 是最终答复 |
    | `error` | 执行出错，data.message 是原因 |
    """
    text = (body.task_input or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="task_input 不能为空")

    thread_id = (body.thread_id or "").strip() or new_thread_id("cs")
    runner = _runner(agent)
    logger.info(f"[{agent.user_id}] 会话 {thread_id}：{text[:40]}")

    return StreamingResponse(
        _stream({"task_input": text}, runner.thread_config(thread_id), runner),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/chat/resume", summary="提交人工审批结果（SSE）")
async def chat_resume(body: ResumeRequest, agent: AgentIdentity = Depends(require_agent)):
    """恢复一次被 interrupt 挂起的会话。

    ⚠️ 必须用**同一个 thread_id**，且恢复时要重发 user_id / external_id ——
    它们不从检查点恢复，漏发会让下游节点 KeyError（见 `demo/runner.py`）。
    这里由 `runner.thread_config()` 统一构造，不手拼 config。
    """
    runner = _runner(agent)
    logger.info(f"[{agent.user_id}] 审批会话 {body.thread_id}：{body.decision}")

    return StreamingResponse(
        _stream(runner.resume_command(body.decision),
                runner.thread_config(body.thread_id), runner),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/state/{thread_id}", summary="回查会话状态")
async def get_state(thread_id: str, agent: AgentIdentity = Depends(require_agent)):
    """读取某个会话的当前状态（不推进执行）。

    刷新页面后要靠它把界面恢复成中断前的样子，否则审批卡片一刷新就没了。
    """
    runner = _runner(agent)
    config = runner.thread_config(thread_id)
    snapshot = runner.graph.get_state(config)
    values = _plain(dict(snapshot.values or {}))
    interrupts = [
        {"id": str(getattr(i, "id", "") or ""), "value": _plain(getattr(i, "value", i))}
        for i in (snapshot.interrupts or [])
    ]
    return {
        "thread_id": thread_id,
        "interrupted": bool(interrupts),
        "interrupts": interrupts,
        "task_result": values.get("task_result"),
        "values": values,
    }


@router.get("/samples", summary="演示用的示例诉求")
async def samples():
    """给工作台页面用的「一键提问」清单。

    直接取命令行那张场景表（`demo.scenarios`），只挑适合做成按钮的那些 ——
    故障注入、需要提交两次才看得出效果的都不在其中。两处各维护一份示例问题，
    迟早会出现「CLI 里能跑、页面上点了报错」的错位。

    这里不走 `quickstart`：那是个 CLI 脚本，import 它得靠 cwd 在 `sys.path` 上，
    而 uvicorn 这类控制台脚本的 `sys.path[0]` 是 `.venv/Scripts` —— 会静默
    返回空清单（页面照常打开，只是示例按钮一个都没有）。改成从 `src` 下的包
    导入，与 cwd 无关。
    """
    try:
        from demo.scenarios import ui_samples
    except Exception as e:  # pragma: no cover - 页面不能因为示例清单挂了就打不开
        logger.warning(f"读取示例场景失败：{e}")
        return {"samples": []}

    return {"samples": ui_samples()}
