"""
MCP 工具调用的**信封协议**：业务失败与协议失败的分界。

为什么不能直接让工具抛异常
--------------------------
MCP 协议层只有一个 `is_error` 字段表示「工具执行出错」，它区分不出下面两种情况：

    「物料 M100023 名下没有被引用的采购单」 —— 业务结论，要如实转述
    「ERP 接口超时」                       —— 系统故障，要降级或快速失败

这两件事在装载链路里的处理路径完全不同。如果都走 `is_error`，客户端只能拿到
一句 `Error executing tool get_purchase_order`，连是「查不到」还是「挂了」都
分不清 —— 而这恰恰是装载节点里 `degraded` 与「这单不存在」两条分支的判据。

所以约定一个信封：**工具永远正常返回**，业务结果装在 `data` 里，业务失败装在
`error` 里。协议层的 `is_error` 只留给「工具实现本身崩了」这种真异常。

    {"ok": true,  "data": <上游原样报文>}
    {"ok": false, "error": "not_found|unavailable|invalid|internal", "message": "..."}

信封里额外带 `exception`（原始异常的类名）
-----------------------------------------
错误码只有四类，而 **not_found 在写链路里对应四个不同的异常**
（MaterialNotFound / SupplierNotFound / PurchaseOrderNotFound / ReceiptNotFound）。
只凭码 + 一段文案，客户端还原不出直连路径上的准确异常类，只能猜一个。
所以 `wrap_error` 把类名一并写进信封，`unwrap` 在 not_found 分支按名字还原 ——
`TOOL_GATEWAY=direct|mcp` 的异常类因此逐一对齐。名字认不出来时退回调用点
指定的 `not_found_cls`（老服务端 + 新客户端也能跑）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from integrations.ports import (
    IntegrationError,
    MaterialNotFound,
    PurchaseOrderNotFound,
    ReceiptNotFound,
    SupplierNotFound,
)
from utils.agent_logger import get_logger

logger = get_logger("mcp_envelope")

# ---------- 错误码 ----------

#: 业务上「查不到」：写链路引用的物料 / 供应商 / 采购单不存在。**不是故障**。
ERROR_NOT_FOUND = "not_found"
#: 上游或网关不可用：网络失败、超时、服务未启动。**是故障**。
ERROR_UNAVAILABLE = "unavailable"
#: 入参不合法：客户端传了上游不认的参数。
ERROR_INVALID = "invalid"
#: 兜底：工具实现内部崩了（应当只出现在真正的 bug 上）。
ERROR_INTERNAL = "internal"

ALL_ERROR_CODES = (ERROR_NOT_FOUND, ERROR_UNAVAILABLE, ERROR_INVALID, ERROR_INTERNAL)

#: 错误码 -> 异常类。`not_found` 不在表里：它对应哪个异常取决于**是哪个端口**
#: （查单条主数据查不到该返回 None，写链路引用缺失实体该抛 *NotFound），
#: 由客户端调用处指定或按信封里的类名还原。
_EXCEPTION_BY_CODE: Dict[str, Type[IntegrationError]] = {
    ERROR_UNAVAILABLE: IntegrationError,
    ERROR_INVALID: IntegrationError,
    ERROR_INTERNAL: IntegrationError,
}

#: 四类 NotFound 的类名 -> 类，供 `unwrap` 按信封里的 `exception` 字段还原。
#: 只登记「业务上查不到」的异常；CircuitOpen 等 IntegrationError 子类不在这里 ——
#: 它们属于 unavailable，还原成通用 IntegrationError 即可（消息里有完整说明）。
_NOT_FOUND_BY_NAME: Dict[str, Type[IntegrationError]] = {
    cls.__name__: cls
    for cls in (MaterialNotFound, SupplierNotFound, PurchaseOrderNotFound, ReceiptNotFound)
}


def error_code_of(exc: BaseException) -> str:
    """把端口抛出的异常翻译成错误码。

    ⚠️ 子类必须排在父类前面：四类 `*NotFound` 都是 `IntegrationError` 的子类，
    顺序颠倒会把「查不到」误判成「上游挂了」。熔断的 `CircuitOpen` 也是
    `IntegrationError` 子类，不需要特判 —— 它本来就该落在 unavailable。
    """
    if isinstance(exc, (MaterialNotFound, SupplierNotFound,
                        PurchaseOrderNotFound, ReceiptNotFound)):
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
    """失败信封。带上原始异常类名（见模块说明的 `exception` 字段）。"""
    resolved = code or error_code_of(exc)
    message = str(exc) or exc.__class__.__name__
    logger.warning(f"MCP 工具返回业务失败（{resolved}）：{message}")
    return {
        "ok": False,
        "error": resolved,
        "message": message,
        "exception": exc.__class__.__name__,
    }


def unwrap(
    envelope: Any,
    *,
    tool: str = "",
    not_found_cls: Optional[Type[IntegrationError]] = None,
) -> Any:
    """拆信封：成功返回 data，失败抛回对应的异常。

    `not_found_cls=None` 表示「这个端口的查不到 = 正常返回空值」——
    读路径就是这样：`get_material` / `get_purchase_order` 查不到该返回
    `None`，让上层去决定怎么措辞。

    写路径反过来：调用点指定 `not_found_cls`（not_found 是异常），
    此时若信封里带了能还原的异常类名（`exception` 字段），优先用它 ——
    这样 `TOOL_GATEWAY=direct|mcp` 抛出的异常类逐一对齐。
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
        restored = _NOT_FOUND_BY_NAME.get(str(envelope.get("exception") or ""))
        raise (restored or not_found_cls)(message or f"{tool} 查不到数据")

    exc_cls = _EXCEPTION_BY_CODE.get(code, IntegrationError)
    raise exc_cls(message or f"MCP 工具 {tool} 调用失败（{code}）")
