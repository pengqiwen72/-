"""
mock 的故障注入开关。

「部分失败不整体失败」如果只写在注释里，就是一句没法验证的话。两个环境变量
让它变成可以当场跑出来的行为：

    MOCK_LATENCY_MS=400          每个业务端口调用都慢 400 毫秒（SSE 进度条可见，
                                 也能直观看出并行确实省时间）
    MOCK_FAIL_ACTIONS=inventory  让库存这一路直接抛错，演示降级分支

`MOCK_FAIL_ACTIONS` 针对的是**动作名**（按端口命名：material / supplier /
warehouse / inventory / purchase / receipt …），不是某个单号。演示要的是
「库存这一路挂了」，而不是「这一单刚好查不到」—— 后者是业务上的空结果，
走的是另一条分支（NotFound / 空结果），两者必须能分别演示。

`graph` 是唯一一个**不属于端口**的动作名：它的判定在 `kg/query.py::_guard()`
里，注入「图谱服务不可用」，用来演示问答链路的 L1 降级（页脚与级别见
`observability/degrade.py`）。开关是同一个，语义一致 —— 不另造强制开关。
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
