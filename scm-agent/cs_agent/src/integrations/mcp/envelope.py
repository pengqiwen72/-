"""
MCP 工具调用的**信封协议**：业务失败与协议失败的分界。

为什么不能直接让工具抛异常
--------------------------
MCP 协议层只有一个 `is_error` 字段表示「工具执行出错」，它区分不出下面两种情况：

    「订单 SO20260318002 没有物流轨迹」  —— 业务结论，要如实告诉客户「还没发货」
    「快递查询接口超时」                —— 系统故障，要降级并转人工

这两件事在客服链路里的处理路径完全不同。如果都走 `is_error`，客户端只能拿到
一句 `Error executing tool get_logistics`，连是「查不到」还是「挂了」都分不清 ——
而这恰恰是订单查询里 `degraded` 与「暂无轨迹」两条分支的判据。

所以约定一个信封：**工具永远正常返回**，业务结果装在 `data` 里，业务失败装在
`error` 里。协议层的 `is_error` 只留给「工具实现本身崩了」这种真异常。

    {"ok": true,  "data": <上游原样报文>}
    {"ok": false, "error": "not_found|unavailable|invalid|internal", "message": "..."}

错误码到异常的映射只写在这一处：服务端用它把异常翻译成码，客户端用它把码翻译回
异常。两边引用同一份表，才不会出现「服务端抛 LogisticsNotFound、客户端还原成
IntegrationError」这种降级失真 —— 那会让 `degraded` 分支永远走不到。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from integrations.ports import (
    InsufficientStock,
    IntegrationError,
    LogisticsNotFound,
    OrderNotFound,
)
from utils.agent_logger import get_logger

logger = get_logger("mcp_envelope")

# ---------- 错误码 ----------

#: 业务上「查不到」：订单不存在、没有物流轨迹。**不是故障**。
ERROR_NOT_FOUND = "not_found"
#: 上游或网关不可用：网络失败、超时、服务未启动。**是故障**。
ERROR_UNAVAILABLE = "unavailable"
#: 入参不合法：客户端传了上游不认的参数。
ERROR_INVALID = "invalid"
#: 兜底：工具实现内部崩了（应当只出现在真正的 bug 上）。
ERROR_INTERNAL = "internal"

ALL_ERROR_CODES = (ERROR_NOT_FOUND, ERROR_UNAVAILABLE, ERROR_INVALID, ERROR_INTERNAL)

#: 错误码 -> 异常类。`not_found` 不在表里：它对应哪个异常取决于**是哪个端口**
#: （订单查不到该返回 None，物流查不到该抛 LogisticsNotFound），由客户端调用处指定。
_EXCEPTION_BY_CODE: Dict[str, Type[IntegrationError]] = {
    ERROR_UNAVAILABLE: IntegrationError,
    ERROR_INVALID: IntegrationError,
    ERROR_INTERNAL: IntegrationError,
}


def error_code_of(exc: BaseException) -> str:
    """把端口抛出的异常翻译成错误码。

    ⚠️ 子类必须排在父类前面：`LogisticsNotFound` / `OrderNotFound` /
    `InsufficientStock` 都是 `IntegrationError` 的子类，顺序颠倒会把
    「查不到」误判成「上游挂了」。
    """
    if isinstance(exc, (LogisticsNotFound, OrderNotFound, InsufficientStock)):
        return ERROR_NOT_FOUND
    if isinstance(exc, IntegrationError):
        return ERROR_UNAVAILABLE
    if isinstance(exc, (TypeError, ValueError, KeyError)):
        return ERROR_INVALID
    return ERROR_INTERNAL


def wrap(data: Any) -> Dict[str, Any]:
    """成功信封。`data` 就是上游原样报文，中间不做任何字段映射。"""
    return {"ok": True, "data": data}


def wrap_error(exc: BaseException, *, code: Optional[str] = None) -> Dict[str, Any]:
    """失败信封。"""
    resolved = code or error_code_of(exc)
    message = str(exc) or exc.__class__.__name__
    logger.warning(f"MCP 工具返回业务失败（{resolved}）：{message}")
    return {"ok": False, "error": resolved, "message": message}


def unwrap(
    envelope: Any,
    *,
    tool: str = "",
    not_found_cls: Optional[Type[IntegrationError]] = None,
) -> Any:
    """拆信封：成功返回 data，失败抛回对应的异常。

    `not_found_cls=None` 表示「这个端口的查不到 = 正常返回空值」——
    订单侧就是这样：`get_order` 查不到该返回 `None`，让上层去决定怎么措辞。
    """
    if not isinstance(envelope, dict) or "ok" not in envelope:
        raise IntegrationError(
            f"MCP 工具 {tool} 没有返回约定信封，收到的内容：{envelope!r}"
        )

    if envelope.get("ok"):
        return envelope.get("data")

    code = str(envelope.get("error") or ERROR_INTERNAL)
    message = str(envelope.get("message") or "")

    if code == ERROR_NOT_FOUND:
        if not_found_cls is None:
            return None
        raise not_found_cls(message or f"{tool} 查不到数据")

    exc_cls = _EXCEPTION_BY_CODE.get(code, IntegrationError)
    raise exc_cls(message or f"MCP 工具 {tool} 调用失败（{code}）")
