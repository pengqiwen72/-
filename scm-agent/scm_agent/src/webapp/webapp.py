"""
演示工作台的 HTTP 入口。

两种起法都行：

    # 1) 独立起（不需要平台、不需要 Redis/Postgres）
    uvicorn src.webapp.webapp:app --port 8000
    # 打开 http://127.0.0.1:8000/scm/workbench/

    # 2) 挂在 LangGraph 平台服务上（见 langgraph.json 的 http.app）
    langgraph dev            # 这个 app 会被挂到 :2024 上，路由不变

路由分工：
  * `/scm/*`  业务接口（登录、流式对话、审批、状态回查）—— 见 `chat_routes.py`
  * 工作台页面与静态资源 —— 见 `static/`

平台自己的路由不在这棵树里，由 `langgraph dev` 提供；这里所有路径都带 `/scm`
前缀，正是为了不和平台路由撞车（存储客户端 `scm_admin/scm_store_api` 认的
`/scm/get_token` 也是这个前缀，改了前缀它就连不上）。
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from utils.agent_logger import get_logger
from utils.env_loader import load_agent_env
from webapp.chat_routes import router as scm_router

load_agent_env()  # 统一入口，别裸调 load_dotenv()（见 utils/env_loader.py）

logger = get_logger("webapp")

#: 工作台静态页目录（`/scm/workbench/`）
#: （词法 parent，不写 .resolve()：见 utils/env_loader.py 的说明）
WEB_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时把三个开关打出来：这个服务能离线跑、也能接真实大模型和真实业务系统，
    # 排障第一步永远是先确认它到底跑在哪套配置上
    logger.info(
        "scm-agent 演示工作台启动 | LLM_MODE=%s INTEGRATION_MODE=%s TOOL_GATEWAY=%s",
        os.getenv("LLM_MODE", "offline"),
        os.getenv("INTEGRATION_MODE", "mock"),
        os.getenv("TOOL_GATEWAY", "direct"),
    )
    yield
    logger.info("scm-agent 演示工作台已停止")


app = FastAPI(
    title="供应链采购仓储智能系统 · 演示工作台",
    description="LangGraph 多链路编排 + RAG + HITL 人工审批",
    version="1.0.0",
    lifespan=lifespan,
)


class TimingMiddleware(BaseHTTPMiddleware):
    """记录请求耗时，慢请求升级为 warning。

    SSE 的耗时约等于「首字节时间」而不是整段推流的时长 —— 对排查
    「点了没反应」这类问题来说，首字节恰恰才是要看的那一段。
    """

    SLOW_SECONDS = 2.0

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started

        response.headers["X-Response-Time-Ms"] = f"{elapsed * 1000:.0f}"
        if elapsed >= self.SLOW_SECONDS:
            logger.warning(f"慢请求 {request.method} {request.url.path} 首字节 {elapsed:.2f}s")
        elif not request.url.path.startswith("/scm/chat"):
            # 流式接口的首字节之外还有很长的推流期，逐条记录只会刷屏
            logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed * 1000:.0f}ms)")
        return response


app.add_middleware(TimingMiddleware)

app.include_router(scm_router)


@app.get("/scm/health", tags=["运维"], summary="健康检查")
async def health():
    """探活。顺带把当前配置带出来，容器里 curl 一下就知道跑的是哪套。"""
    return {
        "status": "ok",
        "llm_mode": os.getenv("LLM_MODE", "offline"),
        "integration_mode": os.getenv("INTEGRATION_MODE", "mock"),
        "tool_gateway": os.getenv("TOOL_GATEWAY", "direct"),
    }


# 工作台页面。/scm/workbench/ 直接出 index.html（html=True 会做这件事），
# 所以页面里所有静态资源引用都要用相对路径，才能跟着这个前缀走。
if WEB_DIR.is_dir():
    app.mount("/scm/workbench", StaticFiles(directory=str(WEB_DIR), html=True), name="workbench")
else:  # pragma: no cover - 静态目录被删掉时不该让整个服务起不来
    logger.warning(f"未找到工作台静态目录：{WEB_DIR}")
