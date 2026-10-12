"""
熔断器（CircuitBreaker）：给「已经在坏的依赖」一条快速失败通道。

要解决的问题
------------
上游挂了的时候，最贵的不是失败本身，而是**每次都要等到超时才知道会失败**。
一个断掉的图谱服务如果每题都等 3 秒，问答链路就变成「答不出来还要卡 3 秒」；
一个宕掉的 ERP 如果每次装载都等 3 秒超时，整条请求都会被拖住。熔断器把
连续失败这个事实记下来：坏到一定程度就直接拒绝，不再去试 —— 等冷却时间
过了再放**一个**探针，探针成功就恢复，失败就继续断着。

状态机（三态，标准语义）
------------------------
    CLOSED    ——1 次失败→ CLOSED   （累计失败；成功一次就清零 —— 「连续失败」
                                 才有意义，偶发失败不该攒出一次熔断）
    CLOSED    ——累计失败 ≥ 阈值→ OPEN
    OPEN      ——冷却期内→ 直接拒绝（快速失败，不调下游）
    OPEN      ——冷却结束→ HALF_OPEN（只放一个探针，防止恢复瞬间被打满）
    HALF_OPEN ——探针成功→ CLOSED（清零）
    HALF_OPEN ——探针失败→ OPEN（重新计时）

接线点（四处，各管一层）
------------------------
1. **端口**：`factory` 构造出的每个业务端口都经 `wrap_port()` 包一层，按
   **动作名**（material / purchase / …）计。三种后端（mock / http / mcp）
   共用同一套熔断名 —— 这样 `TOOL_GATEWAY=direct|mcp` 的行为才逐字段一致，
   而不是「直连会熔断、走网关不会」这种只在某条路径上存在的差异。
2. **图谱**：`kg/query.py::guard()`，名字固定 `graph`（问答链路的图检索与
   供应商履约评估都从它进门）。
3. **HTTP 传输层**：`http/business_http.py::_HttpClient.request`，按上游地址
   计 —— 端口那层管「这个业务能力坏了」，这层管「这个地址坏了」，两层名字
   空间不同：业务层先短路，传输层兜住任何绕过端口的裸调用。
4. （没有第四点。）原本打算在 `mcp/bridge.py::call_tool` 再包一层，评估后
   放弃：端口层的包裹已经把 mcp 后端一并覆盖（Mcp*Port 也由 factory 构造），
   再包一层只会让同一批失败在两个名字下重复计数，还会让 direct/mcp 两模式
   的熔断名对不上。

冷却期与阈值都从环境变量读（`BREAKER_FAIL_THRESHOLD` / `BREAKER_RESET_SECONDS`），
在**构造时**取值 —— 所以 `reset_breakers()` 之后重建的熔断器会带上新环境，
这是场景级演示「现场改冷却时间」的方式（与 `reset_ports()` 同一套路）。
"""

from __future__ import annotations

import os
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

from integrations.ports import IntegrationError
from utils.agent_logger import get_logger

logger = get_logger("resilience")

DEFAULT_FAIL_THRESHOLD = 3
DEFAULT_RESET_SECONDS = 20.0


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        logger.warning(f"{name} 不是整数：{raw!r}，按默认值 {default} 处理")
        return default


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        logger.warning(f"{name} 不是数字：{raw!r}，按默认值 {default} 处理")
        return default


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(IntegrationError):
    """熔断打开期间的快速失败。

    是 `IntegrationError` 的子类：上层已有的 `except IntegrationError` 分支
    （装载节点的兜底、子图的降级分支）不用改一行就能接住它 —— 熔断是
    「上游不可用」的一种，不是一类新的错误。
    """


class CircuitBreaker:
    """单个依赖的熔断器。`name` 是对外可见的身份（日志、异常文案都用它）。"""

    def __init__(
        self,
        name: str,
        *,
        fail_threshold: Optional[int] = None,
        reset_seconds: Optional[float] = None,
    ):
        self.name = name
        self.fail_threshold = (
            fail_threshold if fail_threshold is not None
            else _int_env("BREAKER_FAIL_THRESHOLD", DEFAULT_FAIL_THRESHOLD)
        )
        self.reset_seconds = (
            reset_seconds if reset_seconds is not None
            else _float_env("BREAKER_RESET_SECONDS", DEFAULT_RESET_SECONDS)
        )
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    # ---------- 只读视图（日志与测试用） ----------

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    # ---------- 状态机 ----------

    def allow(self) -> bool:
        """本次调用放不放行。OPEN 里冷却未到 -> False（调用方快速失败）。"""
        with self._lock:
            return self._allow_locked()

    def record_success(self) -> None:
        with self._lock:
            recovered = self._state is not BreakerState.CLOSED
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._probe_in_flight = False
        if recovered:
            logger.info(f"熔断恢复：{self.name} 探针成功，状态闭合")

    def record_failure(self) -> None:
        with self._lock:
            if self._state is BreakerState.OPEN:
                # 打开期间的再次失败（多半来自上层没接快速失败的路径）：
                # 只把冷却时间往后续，不重复计数、不重复告警 —— 否则日志刷屏，
                # 而「一直坏着」本来就该一直断着。
                self._opened_at = time.monotonic()
                return

            self._consecutive_failures += 1
            if (
                self._state is BreakerState.HALF_OPEN
                or self._consecutive_failures >= self.fail_threshold
            ):
                self._trip_locked()

    # ---------- 内部 ----------

    def _allow_locked(self) -> bool:
        if self._state is BreakerState.CLOSED:
            return True

        if self._state is BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.reset_seconds:
                self._state = BreakerState.HALF_OPEN
                self._probe_in_flight = True
                logger.info(f"熔断半开：{self.name} 冷却结束，放行一个探针试探")
                return True
            return False

        # HALF_OPEN：同一时间只放一个探针（探针之外一律继续快速失败）
        if not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def _trip_locked(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        self._probe_in_flight = False
        logger.warning(
            f"熔断打开：{self.name} 连续 {self._consecutive_failures} 次失败，"
            f"{self.reset_seconds:g} 秒内快速失败（冷却结束后放一个探针试探）"
        )


# ==================== 进程级注册表 ====================

_BREAKERS: Dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_breaker(name: str) -> CircuitBreaker:
    """按名字取熔断器（不存在则新建）。名字就是身份：动作名 / graph / 上游地址。"""
    with _REGISTRY_LOCK:
        breaker = _BREAKERS.get(name)
        if breaker is None:
            breaker = CircuitBreaker(name)
            _BREAKERS[name] = breaker
        return breaker


def reset_breakers() -> None:
    """清空全部熔断器（测试、场景切换用；由 `factory.reset_ports()` 顺带调用）。

    必须与端口一起重置：注入的故障撤掉之后还留着 OPEN 的熔断器，
    下一个场景会先吃几次「无辜的快速失败」——「恢复」这件事就演不出来了。
    """
    with _REGISTRY_LOCK:
        _BREAKERS.clear()


# ==================== 调用包装 ====================

def guarded(name: str, fn: Callable[[], Any]) -> Any:
    """经熔断器执行一次调用：不放行就快速失败，放行则按结果记成功/失败。"""
    breaker = get_breaker(name)
    if not breaker.allow():
        raise CircuitOpen(
            f"熔断中：{name} 连续 {breaker.consecutive_failures} 次失败，"
            f"快速失败（冷却 {breaker.reset_seconds:g} 秒）"
        )
    try:
        result = fn()
    except Exception:
        breaker.record_failure()
        raise
    breaker.record_success()
    return result


class _GuardedPort:
    """端口代理：把端口上的每个方法调用都过一遍同名熔断器。

    只拦可调用属性；普通属性（label、base_url 这类）原样透传 ——
    代理必须是透明的，否则 `isinstance(port, XxxPort)` 之外的属性访问
    都会出现「direct 有、mcp 没有」的第三种行为。
    """

    def __init__(self, name: str, port: Any):
        self._breaker_name = name
        self._port = port

    def __getattr__(self, item: str) -> Any:
        attr = getattr(self._port, item)
        if not callable(attr):
            return attr

        def _call(*args: Any, **kwargs: Any) -> Any:
            return guarded(self._breaker_name, lambda: attr(*args, **kwargs))

        _call.__name__ = item
        return _call


def wrap_port(name: str, port: Any) -> Any:
    """给端口套上熔断代理（`name` 用动作名，与 `MOCK_FAIL_ACTIONS` 同一套词）。"""
    return _GuardedPort(name, port)
