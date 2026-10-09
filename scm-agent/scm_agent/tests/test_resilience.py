"""
守的是熔断器的状态机与调用包装（`src/integrations/resilience.py`）。

为什么它值得一整份用例
----------------------
熔断器的每个分支只有几个布尔，但组合起来是最容易「改了没感觉」的那类代码：

  * 阈值语义必须是**连续**失败 —— 中间成功一次要清零，否则偶发抖动会攒出熔断；
  * OPEN 期间再次失败只能顺延冷却，不能重复计数（否则每次快速失败都把冷却
    往后推，冷却永不到期）；
  * HALF_OPEN 只放**一个**探针 —— 恢复瞬间把全部流量放进去等于没熔断；
  * 这些行为要经 `guarded()` 与 `wrap_port()` 验证 —— 那是 factory 与
    `kg/query.py` 实际使用的两个入口，而不是直接戳状态机的字段。

场景级联动（`MOCK_FAIL_ACTIONS=material` → 端口前三次真失败第四次快速失败）
在 `tests/test_ports_scm.py` 与 quickstart 的 `load_degraded` 场景里，这里只钉
状态机本身。
"""

from __future__ import annotations

import pytest

from integrations.ports import IntegrationError
from integrations.resilience import (
    BreakerState,
    CircuitBreaker,
    CircuitOpen,
    get_breaker,
    guarded,
    reset_breakers,
    wrap_port,
)


def _tripped(threshold: int = 3, reset: float = 3600.0) -> CircuitBreaker:
    """一个已经跳到 OPEN 的熔断器（冷却默认 1 小时，杜绝测试里意外半开）。"""
    breaker = CircuitBreaker("test", fail_threshold=threshold, reset_seconds=reset)
    for _ in range(threshold):
        breaker.record_failure()
    return breaker


# ---------------------------------------------------------------- 状态机

def test_consecutive_failures_trip_open_and_fast_fail():
    breaker = CircuitBreaker("unit-trip", fail_threshold=3, reset_seconds=3600.0)

    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED, "没到阈值不许断"

    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False, "OPEN 期间必须快速失败"


def test_success_clears_the_run_of_failures():
    """「连续」失败才有意义：成功一次就清零，偶发失败攒不出一次熔断。"""
    breaker = CircuitBreaker("unit-reset", fail_threshold=3, reset_seconds=3600.0)

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    assert breaker.consecutive_failures == 0

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED, "清零之后要重新数满一轮"


def test_failure_while_open_only_extends_the_cooldown():
    """打开期间的再次失败只顺延冷却：不重复计数、不重复告警。"""
    breaker = _tripped()

    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.consecutive_failures == 3, "计数不能越滚越大"
    assert breaker.allow() is False


def test_cooldown_lets_exactly_one_probe_through():
    """冷却结束 → HALF_OPEN 只放一个探针；探针成功 → CLOSED 清零。"""
    breaker = _tripped(reset=0.0)  # 冷却视为已过

    assert breaker.allow() is True
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow() is False, "探针在途，第二个调用要继续快速失败"

    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.consecutive_failures == 0
    assert breaker.allow() is True


def test_probe_failure_trips_back_to_open():
    breaker = _tripped(threshold=2, reset=0.0)

    assert breaker.allow() is True   # 探针
    breaker.record_failure()         # 探针也失败了
    assert breaker.state is BreakerState.OPEN, "探针失败要立刻重新断开"


# ---------------------------------------------------------------- 配置与注册表

def test_threshold_and_cooldown_come_from_env_at_construction(monkeypatch):
    monkeypatch.setenv("BREAKER_FAIL_THRESHOLD", "5")
    monkeypatch.setenv("BREAKER_RESET_SECONDS", "2.5")
    custom = CircuitBreaker("env-custom")
    assert custom.fail_threshold == 5
    assert custom.reset_seconds == 2.5

    # 坏值回落默认，不炸（reset_ports 之后重建会带上新环境 —— 场景切换靠它）
    monkeypatch.setenv("BREAKER_FAIL_THRESHOLD", "not-a-number")
    monkeypatch.setenv("BREAKER_RESET_SECONDS", "")
    fallback = CircuitBreaker("env-fallback")
    assert fallback.fail_threshold == 3
    assert fallback.reset_seconds == 20.0


def test_reset_breakers_gives_a_clean_registry():
    first = get_breaker("unit-registry")
    for _ in range(3):
        first.record_failure()
    assert first.state is BreakerState.OPEN
    assert get_breaker("unit-registry") is first, "同名必须拿到同一个实例"

    reset_breakers()
    second = get_breaker("unit-registry")
    assert second is not first
    assert second.state is BreakerState.CLOSED, "重置后不许带着上一位的 OPEN 状态"


# ---------------------------------------------------------------- 调用包装

def test_guarded_short_circuits_and_skips_downstream():
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise IntegrationError("上游挂了")

    for _ in range(3):
        with pytest.raises(IntegrationError):
            guarded("unit-guarded", boom)
    assert len(calls) == 3

    with pytest.raises(CircuitOpen) as err:
        guarded("unit-guarded", boom)
    assert len(calls) == 3, "快速失败之后下游一次都不能再被触达"
    assert "冷却" in str(err.value), "文案要说清为什么这次没去试"

    assert issubclass(CircuitOpen, IntegrationError), "上层已有的降级分支要能接住它"


def test_wrap_port_is_transparent_and_counts_calls_by_action():
    class FakePort:
        label = "物料"          # 非可调用属性，必须原样透传

        def __init__(self):
            self.calls = 0

        def ping(self):
            self.calls += 1
            return {"pong": True}

        def list_materials(self):
            self.calls += 1
            raise IntegrationError("上游挂了")

    inner = FakePort()
    port = wrap_port("unit-wrapped", inner)

    assert port.label == "物料", "代理不能改变属性访问行为"
    assert port.ping() == {"pong": True}, "成功路径对调用方完全透明"

    for _ in range(3):
        with pytest.raises(IntegrationError):
            port.list_materials()
    with pytest.raises(CircuitOpen):
        port.list_materials()

    assert inner.calls == 4, "3 次真失败 + 1 次 ping；熔断打开后不能再触达下游"
