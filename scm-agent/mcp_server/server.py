"""
网关暴露的十八个供应链业务工具（物料 / 供应商 / 仓库 / 库存 / 采购 / 收货 / 工单）。

工具契约
--------
**永远正常返回，业务失败装在信封里**：

    {"ok": true,  "data": <上游原样报文>}
    {"ok": false, "error": "not_found|unavailable|invalid|internal", "message": "..."}

不这么做的代价见 `scm_agent/src/integrations/mcp/envelope.py` 的注释：
MCP 只有一个 `is_error`，分不出「写链路引用的物料不存在」和「ERP 接口超时」，
而这两件事一个要如实转述、一个要降级或快速失败。

三个刻意的留白
--------------
1. **原样转发，不做字段映射。** `list_materials` 吐出来的还是 ERP 那套
   `matCode` / `refPrice`（分）/ `orderStatus`（两位码）。归一化留在 Agent 侧：
   网关一旦开始改名换单位，就等于在三套端口实现之间凭空插进第四套口径，
   而它只在 `TOOL_GATEWAY=mcp` 时才生效。
2. **不做业务判断。** 不存在的东西直接问上游，网关不预先查一遍再说「不存在」；
   `list_supplier_quotes` 只给报价事实，不排名 —— 评分口径属于 Agent 侧
   （sub_supplier）与图谱侧（kg/query.py），网关再放一份就是第三个副本。
3. **不做缓存。** 缓存要按「谁的数据、多久算旧」来定，那是业务决策；
   网关只负责把请求转给真实系统。

反过来，网关**该**管的是横切关注点：鉴权、限流、审计、超时、重试、
把内部系统的报错翻译成稳定的错误码。这些加在这里，Agent 侧和别的 MCP
客户端都能白拿一份 —— 这也是把工具层单独拆出来的收益所在。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from integrations.mcp.envelope import wrap, wrap_error

server = MCPServer(
    name="scm-business-tools",
    title="供应链业务工具网关",
    version="1.0.0",
    instructions=(
        "面向供应链采购仓储场景的业务工具集：查物料、查供应商与报价、查仓库与库存、"
        "下达采购订单、登记收货、建转人工工单。\n"
        "所有工具都返回统一信封：成功时 data 里是上游业务系统的原样报文"
        "（ERP/SRM/WMS 各自的字段口径，未做归一），"
        "失败时 error 给出 not_found / unavailable / invalid / internal 四类错误码，"
        "并附原始异常类名（exception 字段）。\n"
        "注意区分 not_found（业务上查不到 / 写链路引用了不存在的实体）与 unavailable"
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


def _material():
    from integrations.factory import get_material_port

    return get_material_port()


def _supplier():
    from integrations.factory import get_supplier_port

    return get_supplier_port()


def _warehouse():
    from integrations.factory import get_warehouse_port

    return get_warehouse_port()


def _inventory():
    from integrations.factory import get_inventory_port

    return get_inventory_port()


def _purchase():
    from integrations.factory import get_purchase_port

    return get_purchase_port()


def _receipt():
    from integrations.factory import get_receipt_port

    return get_receipt_port()


def _handoff():
    from integrations.factory import get_handoff_port

    return get_handoff_port()


# ==================== 物料 ====================

@server.tool(description="查询全量物料主数据（ERP）。返回上游原样报文（matCode/matName/specModel/matGroup/baseUnit/refPrice 分/substituteCodes）。装载与全量同步用。")
def list_materials() -> Dict[str, Any]:
    return _run(lambda: _material().list_materials())


@server.tool(description="按关键词搜索物料（编码或名称片段，如「冷轧钢板」「M100023」）。口语化指称落到具体物料时用这个。")
def search_materials(keyword: str, limit: int = 20) -> Dict[str, Any]:
    return _run(lambda: _material().search_materials(keyword, limit))


@server.tool(description="按物料编码查询单条物料主数据。物料不存在时返回 ok=false, error=not_found。")
def get_material(material_code: str) -> Dict[str, Any]:
    return _run(lambda: _material().get_material(material_code))


@server.tool(description="查询某物料登记在册的替代料（返回替代料自身的主数据）。没有登记替代料时返回空列表 —— 那是业务上的「没有」，不是故障。")
def list_substitutes(material_code: str) -> Dict[str, Any]:
    return _run(lambda: _material().list_substitutes(material_code))


# ==================== 供应商 ====================

@server.tool(description="查询全量供应商档案（SRM）。返回含履约指标的报文（supCode/isNewSup/leadDays/onTimeRate/defectRate）。")
def list_suppliers() -> Dict[str, Any]:
    return _run(lambda: _supplier().list_suppliers())


@server.tool(description="按供应商编码查询单条档案。供应商不存在时返回 ok=false, error=not_found。")
def get_supplier(supplier_code: str) -> Dict[str, Any]:
    return _run(lambda: _supplier().get_supplier(supplier_code))


@server.tool(description="查询某物料各家供应商的报价（SRM）。只给报价事实（quotPrice 分/minOrderQty/validDays），不做评分排名 —— 排名口径在 Agent 侧。")
def list_supplier_quotes(material_code: str) -> Dict[str, Any]:
    return _run(lambda: _supplier().list_supplier_quotes(material_code))


# ==================== 仓库 / 库存 ====================

@server.tool(description="查询全量仓库档案（WMS）：whCode/whName/city/addr。")
def list_warehouses() -> Dict[str, Any]:
    return _run(lambda: _warehouse().list_warehouses())


@server.tool(description="查询全量库存快照（WMS，物料 × 仓库）。上游给四个原始量（onHandQty/frozenQty/safetyQty/avgOut30d），可用量由业务侧计算。")
def list_stock() -> Dict[str, Any]:
    return _run(lambda: _inventory().list_stock())


@server.tool(description="查询某物料在各仓库的库存分布。库存查询与调拨判断的输入。")
def list_stock_by_material(material_code: str) -> Dict[str, Any]:
    return _run(lambda: _inventory().list_stock_by_material(material_code))


@server.tool(description="查询某仓库里各物料的库存（盘点视角）。")
def list_stock_by_warehouse(warehouse_code: str) -> Dict[str, Any]:
    return _run(lambda: _inventory().list_stock_by_warehouse(warehouse_code))


# ==================== 采购 ====================

@server.tool(description=(
    "下达采购订单（ERP，写操作）。request 字段：request_id、material_code、qty、unit、"
    "unit_price（元）、amount（元）、supplier_code、target_warehouse、operator_id、"
    "idempotency_key（**必填**，同一笔业务重复提交必须复用同一个键，否则会重复下单）。"
    "引用的物料 / 供应商不存在时返回 ok=false, error=not_found。"
))
def create_purchase_order(request: Dict[str, Any]) -> Dict[str, Any]:
    return _run(lambda: _purchase().create_purchase_order(dict(request or {})))


@server.tool(description="按采购单号回查采购订单（原材料 + 运行期下达的单）。查不到返回 ok=false, error=not_found。")
def get_purchase_order(po_no: str) -> Dict[str, Any]:
    return _run(lambda: _purchase().get_purchase_order(po_no))


@server.tool(description="查询全量采购订单（种子单 + 运行期下达的单，按单号排序）。装载链路用。")
def list_purchase_orders() -> Dict[str, Any]:
    return _run(lambda: _purchase().list_purchase_orders())


# ==================== 收货 / 工单 ====================

@server.tool(description=(
    "登记收货并归档质检报告（WMS，写操作）。request 字段：po_no、qty_received、qty_defect、"
    "defect_type、disposition、warehouse_code、inspector、"
    "idempotency_key（**必填**）。应收数量与供应商由采购单反查，无需复述。"
    "引用的采购单不存在时返回 ok=false, error=not_found。"
))
def submit_receipt(request: Dict[str, Any]) -> Dict[str, Any]:
    return _run(lambda: _receipt().submit_receipt(dict(request or {})))


@server.tool(description="按收货单号回查收货记录（含质检报告 URL，若有归档）。查不到返回 ok=false, error=not_found。")
def get_receipt(receipt_no: str) -> Dict[str, Any]:
    return _run(lambda: _receipt().get_receipt(receipt_no))


@server.tool(description=(
    "创建转人工工单。request 字段：reason（转人工原因）、summary（简要描述）、"
    "priority、thread_id、operator_id。"
    "机器人在置信度不足、涉及敏感诉求或多次兜底失败时建单。"
))
def create_ticket(request: Dict[str, Any]) -> Dict[str, Any]:
    return _run(lambda: _handoff().create_ticket(dict(request or {})))


# ==================== 连通性 ====================

@server.tool(description="连通性探测：返回网关进程状态与后端模式（INTEGRATION_MODE）。排查「网关是否活着、背后指向 mock 还是真实系统」时先调它。")
def ping() -> Dict[str, Any]:
    return _run(lambda: {
        "status": "ok",
        "server": "scm-business-tools",
        "integration_mode": (os.getenv("INTEGRATION_MODE") or "mock").strip().lower(),
        # 网关进程永远直连（bridge 侧强制 TOOL_GATEWAY=direct），列出来是让调用方看见
        "tool_gateway": "direct",
    })


def main() -> None:
    """以 stdio 启动网关（由 Agent 侧拉起，不单独开端口）。"""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
