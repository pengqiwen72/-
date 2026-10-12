"""统一的 `.env` 装载入口 —— 所有模块都从 `load_agent_env()` 走，别裸调 `load_dotenv()`。

为什么不能裸调
--------------
`load_dotenv()` 不传路径时会走 `find_dotenv()`，后者内部调 `os.getcwd()`。
而 `langgraph dev` 的运行时开着 blockbuster 守卫 —— 它只在**当前线程正跑着事件
循环**时才拦（见 `blockbuster/blockbuster.py` 的 wrapper：`asyncio.get_running_loop()`
抛 RuntimeError 即放行），而 `os.getcwd()` 恰在被拦名单里。于是：

* 模块在**服务启动时**就 import 的（`webapp.py`、`auth.py`）→ 碰不上守卫，一直没事；
* 模块在**请求上下文里**才首次 import 的（`scm_graph.py` —— 演示工作台首请求才建图）
  → 整个请求直接 500：

      blockbuster.blockbuster.BlockingError: Blocking call to os.getcwd

真踩过一次（`POST /scm/chat/stream` 全程 500），所以统一收敛到显式路径：显式路径
不触发 `find_dotenv()`，文件读本身也不在拦截名单里（langgraph 明确放行了 IO）。
顺带解决另一个问题：无论从哪个目录启动，读到的都是同一份 `scm_agent/.env`。

边界（别顺手改坏）
------------------
* 守卫只拦**有事件循环的线程**：图执行走 Starlette 给同步生成器开的线程池
  （`webapp/chat_routes.py` 的 `_stream` 是同步生成器），那里 `time.sleep` 不报错 ——
  所以 `integrations/mock/chaos.py` 里 `MOCK_LATENCY_MS` 的 `time.sleep` 是安全的，
  不要为了「异步安全」把它改成 `asyncio.sleep`（mock 适配器是同步接口，改不动）。
* `interrupt()` 恢复、图执行这些都在线程池里；**只有路由函数体本身**（`async def`）
  在事件循环上，那里出现的阻塞调用才是真问题。
* 模块级路径常量一律用**词法** `Path(__file__).parents[n]`，不要 `.resolve()` ——
  Windows 上 `ntpath.realpath` 内部还是会调 `os.getcwd()`，照拦不误（与裸
  `load_dotenv()` 是同一个坑，本仓库 6 处路径常量都已改成词法写法）。
  实测放行 / 拦截（`langgraph dev` 的真实配置）：

      放行  Path(__file__) / .parent / .parents[n] / .absolute() / .exists()
      放行  open(path) / json.load(...)        # os.stat、os.path.* 被显式免检
      拦截  .resolve() / 相对路径的 os.path.abspath()   # 都是 os.getcwd 的锅
      拦截  time.sleep / os.mkdir / socket.* / sqlite3.*  # 只在线程池里调用才合法
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

#: 本文件在 `src/utils/` 下，上溯两级即 `scm_agent/` 根目录。
#: 用 `parents` 纯词法取路径（不调 `os.getcwd` / `resolve`），免得又引进一个被拦调用。
AGENT_ROOT = Path(__file__).parents[2]

#: 与 README 里 `cp .env.example .env` 的落点保持一致。
ENV_PATH = AGENT_ROOT / ".env"


def load_agent_env() -> Path:
    """装载 `scm_agent/.env`（override=False，真实环境变量优先），返回路径。

    文件不存在时静默跳过 —— 与 `load_dotenv()` 原语义一致：不配 `.env` 也能跑，
    靠的是代码里的默认值。
    """
    load_dotenv(dotenv_path=ENV_PATH)
    return ENV_PATH
