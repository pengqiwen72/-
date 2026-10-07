"""
mock 的故障注入开关。

「部分失败不整体失败」如果只写在注释里，就是一句没法验证的话。两个环境变量
让它变成可以当场跑出来的行为：

    MOCK_LATENCY_MS=400          每个业务端口调用都慢 400 毫秒（SSE 进度条可见，
                                 也能直观看出并行确实省时间）
    MOCK_FAIL_ACTIONS=logistics  让物流这一路直接抛错，演示降级分支

`MOCK_FAIL_ACTIONS` 针对的是**动作名**（order / logistics / inventory / refund），
不是某个订单号。演示要的是「物流这一路挂了」，而不是「这一单刚好查不到」——
后者是业务上的空结果，走的是另一条分支（`OrderNotFound` / `LogisticsNotFound`），
两者必须能分别演示。
"""

from __future__ import annotations

import os
import time

from integrations.ports import IntegrationError
from utils.agent_logger import get_logger

logger = get_logger("mock_chaos")


def _latency_ms() -> int:
    raw = (os.getenv("MOCK_LATENCY_MS") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except ValueError:
        logger.warning(f"MOCK_LATENCY_MS 不是数字：{raw!r}，按 0 处理")
        return 0


def failing_actions() -> set:
    raw = (os.getenv("MOCK_FAIL_ACTIONS") or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def apply(action: str) -> None:
    """在每次端口调用前执行：先按配置睡一会儿，再决定要不要失败。

    延迟放在失败之前 —— 一个「先超时才失败」的上游更接近真实，
    也让 demo 里能看到「等了一会儿才降级」而不是瞬间返回。
    """
    delay = _latency_ms()
    if delay:
        time.sleep(delay / 1000.0)

    if action.lower() in failing_actions():
        raise IntegrationError(
            f"[故障注入] MOCK_FAIL_ACTIONS 命中 {action}，模拟上游不可用"
        )
