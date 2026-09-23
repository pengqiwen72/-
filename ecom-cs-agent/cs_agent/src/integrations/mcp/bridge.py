"""
MCP stdio 会话桥：把异步的 MCP SDK 接进同步的 LangGraph 节点。

问题
----
MCP Python SDK 是 async 的（`stdio_client` + `ClientSession` 都是异步上下文管理器），
而这个项目的节点全是同步函数 —— 而且订单查询还会在 `ThreadPoolExecutor` 里
并发调端口。直接用 `asyncio.run()` 一调一次是不行的：

  1. 每调一次就要重启一个 MCP 服务进程（Python 解释器启动 + 导入 ≈ 1 秒），
     单次订单查询要调三路，光进程启动就把「并行拉取」的收益吃干净了；
  2. `asyncio.run()` 不能在已有事件循环的线程里调用，而 LangGraph 的节点
     随时可能跑在事件循环里。

做法
----
一个**常驻**的专属事件循环线程 + 一个**常驻**的 stdio 会话：

    主线程 / 工作线程          bridge 线程
    ─────────────────          ───────────
    call()  ──run_coroutine_threadsafe──▶  asyncio 事件循环
             ◀──────── result ──────────   └─ ClientSession（长连接）
                                              └─ mcp_server 子进程（常驻）

`run_coroutine_threadsafe` 本身是线程安全的，多个工作线程可以并发调用同一个会话。

为什么把 stderr 接进日志
------------------------
子进程的报错默认会直接打到父进程的终端上。demo 的输出是精心排版的，被一段
MCP 服务端 traceback 拦腰截断很难看。所以这里把它的 stderr 逐行转进 logger：
默认只在 DEBUG 级别可见，出问题时把 `LOG_LEVEL=DEBUG` 打开就能看到原文。
"""

from __future__ import annotations

import asyncio
import atexit
import os
import time
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils.agent_logger import get_logger

logger = get_logger("mcp_bridge")

#: 仓库根目录（`mcp_server/` 就在这一层）。
#: src/integrations/mcp/bridge.py -> parents[4]
REPO_ROOT = Path(__file__).resolve().parents[4]

#: 单次工具调用超时（秒）。超时按「上游不可用」处理，走降级而不是把整条链路拖死。
DEFAULT_CALL_TIMEOUT = 10.0

#: 会话建立超时（秒）。含子进程冷启动。
START_TIMEOUT = 30.0

SDK_HINT = (
    "未安装 MCP SDK。启用 MCP 网关需要它：\n"
    "    uv pip install 'mcp>=2.0'\n"
    "（或 pip install mcp；它也是 pyproject 里的可选依赖组 `mcp`）\n"
    "不想装就继续用默认的 TOOL_GATEWAY=direct，功能完全一样。"
)


def call_timeout() -> float:
    raw = (os.getenv("MCP_CALL_TIMEOUT_SECONDS") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_CALL_TIMEOUT
    except ValueError:
        return DEFAULT_CALL_TIMEOUT


def server_command() -> List[str]:
    """启动网关进程的命令。

    `MCP_SERVER_COMMAND` 可覆盖，用来指向别的 MCP server（比如第三方工具集），
    这也是换供应商不动代码的那条缝。
    """
    override = (os.getenv("MCP_SERVER_COMMAND") or "").strip()
    if override:
        return override.split()
    return [sys.executable, "-m", "mcp_server"]


def _server_env() -> Dict[str, str]:
    """传给子进程的环境变量。

    整体复制父进程环境是必须的：`INTEGRATION_MODE`、`MOCK_FAIL_ACTIONS`、
    `MOCK_LATENCY_MS` 这些开关要能穿到网关进程里去，否则
    「demo 里注入故障 → 网关侧却没坏」会让降级分支看起来像假的。

    ⚠️ 强制 `TOOL_GATEWAY=direct`：网关自己绝不能再走一次网关，否则就是
    自己调自己，直接递归到栈溢出。
    """
    env = dict(os.environ)
    env["TOOL_GATEWAY"] = "direct"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _describe(exc: BaseException) -> str:
    """把异常说成人话。

    anyio 的 TaskGroup 会把真正的原因裹进 `ExceptionGroup`，直接 str() 出来
    只有一句 `unhandled errors in a TaskGroup (1 sub-exception)` —— 网关起不来
    的时候，这句话等于没说。所以递归拆到最里面那层。
    """
    if isinstance(exc, BaseExceptionGroup):  # py3.11+
        inner = "; ".join(_describe(e) for e in exc.exceptions)
        return inner or exc.__class__.__name__
    text = str(exc)
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__


def _log_line(line: str) -> None:
    """服务端 traceback 走 warning，其余走 debug —— 正常运行时不该刷屏。"""
    if not line.strip():
        return
    lowered = line.lower()
    if "traceback" in lowered or "error" in lowered:
        logger.warning(f"[mcp_server] {line}")
    else:
        logger.debug(f"[mcp_server] {line}")


class _StderrPump:
    """把网关子进程的 stderr 逐行转进 logger。

    ⚠️ 不能传一个「类文件对象」给 SDK 的 `errlog` 参数：它最后是
    `Popen(stderr=errlog)`，**只会取 fileno**。传个自制的 IOBase 进去，
    真实报错是一句没头没尾的 `UnsupportedOperation: fileno`，
    而它发生在事件循环线程里，看起来就像「MCP 网关莫名其妙启动失败」。

    所以这里走真的管道：写端是个货真价实的文件对象（交出去做 stderr），
    读端由一个守护线程持续读走 —— 不读的话管道塞满 64KB 会把子进程卡死，
    这种死锁比崩溃更难查。
    """

    def __init__(self):
        read_fd, write_fd = os.pipe()
        self._read_fd = read_fd
        # 写端交给子进程当 stderr；文本模式只是图个方便，我们从不往里写
        self.writer = os.fdopen(write_fd, "w", encoding="utf-8", errors="replace")
        self._thread = threading.Thread(
            target=self._pump, name="mcp-gateway-stderr", daemon=True
        )
        self._thread.start()

    def _pump(self) -> None:
        buffer = ""
        while True:
            try:
                chunk = os.read(self._read_fd, 4096)
            except OSError:
                break  # 写端已关闭（Windows 上是 OSError 而不是 EOF）
            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="replace")
            # 只转发完整的行：半行拆开发出去会打断 logger 的格式
            *lines, buffer = buffer.split("\n")
            for line in lines:
                _log_line(line)
        if buffer:
            _log_line(buffer)

    def close(self) -> None:
        for closer in (lambda: self.writer.close(),
                       lambda: os.close(self._read_fd)):
            try:
                closer()
            except OSError:  # pragma: no cover - 重复关闭
                pass
        self._thread.join(timeout=1)


class McpSession:
    """一个常驻的 stdio MCP 会话，对外提供同步的 `call_tool()`。"""

    def __init__(self, command: Optional[Sequence[str]] = None,
                 timeout: Optional[float] = None):
        self._command = list(command or server_command())
        self._timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._session: Any = None
        self._stop_event: Optional[asyncio.Event] = None
        self._closed = False
        self._warmed = False

    # ---------- 生命周期 ----------

    def start(self) -> "McpSession":
        """启动会话（幂等）。首次调用会拉起网关子进程。"""
        if self._thread is not None:
            return self

        self._thread = threading.Thread(
            target=self._run_loop, name="mcp-gateway", daemon=True
        )
        self._thread.start()

        if not self._ready.wait(START_TIMEOUT):
            raise self._fail("MCP 网关会话超时未就绪")
        if self._start_error is not None:
            raise self._fail(
                f"MCP 网关启动失败：{_describe(self._start_error)}\n"
                f"命令：{' '.join(self._command)}\n"
                f"（用 LOG_LEVEL=DEBUG 可以看到网关进程的原始 stderr）"
            )
        return self

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve_forever())
        except BaseException as e:  # noqa: BLE001 —— 要把启动失败原样带回调用线程
            self._start_error = e
            logger.error(f"MCP 网关会话结束：{_describe(e)}")
        finally:
            # 无论成功失败都放行等待者：失败时让它在 start() 里看到 _start_error，
            # 否则调用线程会一直阻塞到 START_TIMEOUT，把一个明确的错误拖成超时
            self._ready.set()

    async def _serve_forever(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:
            raise RuntimeError(SDK_HINT) from e

        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            env=_server_env(),
            cwd=str(REPO_ROOT),
        )

        pump = _StderrPump()
        try:
            async with stdio_client(params, errlog=pump.writer) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._stop_event = asyncio.Event()
                    self._ready.set()
                    logger.info(f"MCP 网关已连接：{' '.join(self._command)}")
                    await self._stop_event.wait()
        finally:
            # 会话结束（含启动失败）才关写端：提前关会让子进程的日志丢失，
            # 而那正是排查启动失败最需要的东西
            self._session = None
            pump.close()

        logger.debug("MCP 网关会话已关闭")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ---------- 调用 ----------

    def call_tool(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """同步调用一个工具，返回拆掉 MCP 包装后的**信封**。

        只负责把协议层的东西翻译掉，业务语义（`ok` / `error`）交给
        `envelope.unwrap()`，因为「查不到算不算失败」要由具体端口决定。
        """
        cold = not self._warmed
        started = time.perf_counter()
        self.start()

        if self._session is None:  # pragma: no cover - start() 保证非空
            raise self._fail("MCP 网关会话未建立")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(tool, arguments), self._loop
            )
            result = future.result(timeout=self._timeout or call_timeout())
        except TimeoutError as e:
            from integrations.ports import IntegrationError

            raise IntegrationError(
                f"MCP 工具 {tool} 超时（>{self._timeout or call_timeout()}s）"
            ) from e
        except Exception as e:  # noqa: BLE001 —— 协议层异常一律转成端口层异常
            from integrations.ports import IntegrationError

            raise IntegrationError(f"MCP 工具 {tool} 调用失败：{e}") from e

        if cold:
            self._warmed = True
            # 第一次调用要付进程冷启动（解释器 + 导入 SDK），把它显式说清楚：
            # 否则 demo 里第一路取数会莫名其妙地慢一秒，看着像接口性能问题
            logger.info(
                f"首个工具调用 {tool} 耗时 {(time.perf_counter() - started) * 1000:.0f}ms"
                f"（含网关进程冷启动，之后每次调用只剩协议往返）"
            )

        return self._decode(tool, result)

    @staticmethod
    def _decode(tool: str, result: Any) -> Dict[str, Any]:
        """把 CallToolResult 还原成信封。

        MCP 的结果有两种载体：`structuredContent`（结构化）和 `content` 里的
        text block。当前 SDK 默认只填后者（工具返回的 dict 会被序列化成 JSON 文本），
        但协议允许前者，所以两个都认 —— 换 SDK 版本时不至于整条链路突然读不到数据。
        """
        import json

        from integrations.ports import IntegrationError

        if getattr(result, "is_error", False):
            text = _first_text(result)
            raise IntegrationError(f"MCP 工具 {tool} 执行出错：{text or '（无错误详情）'}")

        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict) and "ok" in structured:
            return structured

        text = _first_text(result)
        if not text:
            raise IntegrationError(f"MCP 工具 {tool} 返回了空内容")

        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as e:
            raise IntegrationError(f"MCP 工具 {tool} 返回的不是 JSON：{text[:200]}") from e

        if not isinstance(decoded, dict):
            raise IntegrationError(f"MCP 工具 {tool} 返回的不是对象：{decoded!r}")
        return decoded

    def _fail(self, message: str) -> Exception:
        from integrations.ports import IntegrationError

        self.close()
        return IntegrationError(message)


def _first_text(result: Any) -> str:
    for block in (getattr(result, "content", None) or []):
        text = getattr(block, "text", None)
        if text:
            return str(text)
    return ""


# ==================== 进程级单例 ====================

_session: Optional[McpSession] = None
_lock = threading.Lock()


def get_session() -> McpSession:
    """取进程级共享的网关会话。

    多个端口共用一个会话（而不是各开一个子进程）：网关是无状态的工具层，
    为订单开一个、为物流再开一个，只会平白多出几份解释器开销。
    """
    global _session
    with _lock:
        if _session is None or _session._closed:
            _session = McpSession()
        return _session


def reset_session() -> None:
    """关闭并丢弃共享会话（测试与 `reset_ports()` 用）。"""
    global _session
    with _lock:
        if _session is not None:
            _session.close()
            _session = None


atexit.register(reset_session)
