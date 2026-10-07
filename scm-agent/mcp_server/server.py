"""
网关暴露的八个业务工具（订单 / 物流 / 库存 / 售后）。

工具契约
--------
**永远正常返回，业务失败装在信封里**：

    {"ok": true,  "data": <上游原样报文>}
    {"ok": false, "error": "not_found|unavailable|invalid|internal", "message": "..."}

不这么做的代价见 `scm_agent/src/integrations/mcp/envelope.py` 的注释：
MCP 只有一个 `is_error`，分不出「这单没发货所以没有物流轨迹」和「快递接口超时」，
而这两件事在客服链路里一个要如实回答、一个要降级转人工。

三个刻意的留白
--------------
1. **原样转发，不做字段映射。** `get_order` 吐出来的还是订单中台那套
   `orderNo` / `tradeStatus` / `payFee`。归一化留在 Agent 侧：网关一旦开始
   改名换单位，就等于在三套端口实现之间凭空插进第四套口径，而它只在
   `TOOL_GATEWAY=mcp` 时才生效。
2. **不做业务判断。** 不存在的东西直接问上游，网关不预先查一遍再说「不存在」。
3. **不做缓存。** 缓存要按「谁的数据、多久算旧」来定，那是业务决策；
   网关只负责把请求转给真实系统。

反过来，网关**该**管的是横切关注点：鉴权、限流、审计、超时、重试、
把内部系统的报错翻译成稳定的错误码。这些加在这里，Agent 侧和别的 MCP
客户端都能白拿一份 —— 这也是把工具层单独拆出来的收益所在。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from integrations.mcp.envelope import wrap, wrap_error

server = MCPServer(
    name="scm-business-tools",
    title="电商客服业务工具网关",
    version="1.0.0",
    instructions=(
        "面向电商客服场景的业务工具集：查订单、查物流、查库存、发起退款、"
        "查退款单、建转人工工单。\n"
        "所有工具都返回统一信封：成功时 data 里是上游业务系统的原样报文，"
        "失败时 error 给出 not_found / unavailable / invalid / internal 四类错误码。\n"
        "注意区分 not_found（业务上查不到，是正常结论）与 unavailable"
        "（系统故障，应重试或降级）。"
    ),
)


def _run(fn) -> Dict[str, Any]:
    """执行一次端口调用并包成信封。

    只在**这一处**做异常收口：任何一个工具漏了 try，客户端就会拿到
    `is_error=True` 和一句 `Error executing tool xxx` —— 错误码没了，
    上层也就无从分辨该降级还是该如实作答。
    """
    try:
        return wrap(fn())
    except Exception as e:  # noqa: BLE001 —— 业务异常与实现 bug 都要转成信封
        return wrap_error(e)


def _order():
    from integrations.factory import get_order_port

    return get_order_port()


def _logistics():
    from integrations.factory import get_logistics_port

    return get_logistics_port()


def _inventory():
    from integrations.factory import get_inventory_port

    return get_inventory_port()


def _refund():
    from integrations.factory import get_refund_port

    return get_refund_port()


def _handoff():
    from integrations.factory import get_handoff_port

    return get_handoff_port()


# ==================== 订单 ====================

@server.tool(description="按订单号查询订单主数据。返回上游原样报文（orderNo/tradeStatus/payFee/收货地址等）。订单不存在时返回 ok=false, error=not_found。")
def get_order(order_id: str) -> Dict[str, Any]:
    return _run(lambda: _order().get_order(order_id))


@server.tool(description="查询某个客户（unionId）名下的订单列表，按时间倒序。客户问「我的订单」时用这个，别拿手机号当 customer_id。")
def list_orders_by_customer(customer_id: str, limit: int = 20) -> Dict[str, Any]:
    return _run(lambda: _order().list_orders_by_customer(customer_id, limit))


@server.tool(description="按下单时间区间拉订单，start/end 格式为 yyyy-MM-dd（含当天）。数据来自报表库，用于统计与周报，不是实时数据。")
def list_orders_by_range(start: str, end: str, limit: int = 500) -> Dict[str, Any]:
    return _run(lambda: _order().list_orders_by_range(start, end, limit))


# ==================== 物流 ====================

@server.tool(description="按订单号查询物流轨迹。返回承运商、运单号与轨迹节点列表。未发货或查不到轨迹时返回 ok=false, error=not_found —— 这是业务结论，不是故障。")
def get_logistics(order_id: str) -> Dict[str, Any]:
    return _run(lambda: _logistics().get_trace(order_id))


# ==================== 库存 ====================

@server.tool(description="批量查询 SKU 库存。sku_ids 一次可传多个（建议不超过 20 个）以减少往返。warehouse 为空表示全部仓库合计。")
def get_stock(sku_ids: List[str], warehouse: Optional[str] = None) -> Dict[str, Any]:
    return _run(lambda: _inventory().get_stock(sku_ids, warehouse))


# ==================== 售后 ====================

@server.tool(description=(
    "发起退款。request 字段：order_id、action_type（如 auto_refund / manual_refund）、"
    "amount（元）、refund_type、policy_id（依据的售后政策）、"
    "idempotency_key（**必填**，同一笔业务重复提交必须复用同一个键，"
    "否则会重复扣款）。"
))
def submit_refund(request: Dict[str, Any]) -> Dict[str, Any]:
    return _run(lambda: _refund().submit_refund(dict(request or {})))


@server.tool(description="按退款单号查询退款单状态与金额。用于回查之前提交的退款是否已到账。")
def get_refund(refund_id: str) -> Dict[str, Any]:
    return _run(lambda: _refund().get_refund(refund_id))


@server.tool(description=(
    "创建转人工工单。request 字段：customer_id、reason（转人工原因）、"
    "summary（简要描述）、priority、thread_id、contact。"
    "机器人在置信度不足、涉及敏感诉求或多次兜底失败时建单。"
))
def create_ticket(request: Dict[str, Any]) -> Dict[str, Any]:
    return _run(lambda: _handoff().create_ticket(dict(request or {})))


def main() -> None:
    """以 stdio 启动网关（由 Agent 侧拉起，不单独开端口）。"""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
