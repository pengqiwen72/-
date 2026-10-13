"""
供应链业务系统端口（Port）。

用 Protocol 定义接口，把「业务能力」与「调用哪个外部系统」解耦：

    mock 实现 -> 读 data/seed/*.json（按上游报文形状重组），零依赖、零网络，demo 默认
    http 实现 -> 调真实业务系统（ERP / SRM / WMS / OSS），生产走这条
    mcp  实现 -> 经 MCP stdio 网关跨进程调用上面两者之一

八个端口对应八类业务数据源：
    MaterialPort   物料主数据（ERP）
    SupplierPort   供应商与报价（SRM）
    WarehousePort  仓库主数据（WMS）
    InventoryPort  库存快照（WMS）
    PurchasePort   采购订单：下达 / 回查（ERP 采购模块，写链路）
    ReceiptPort    收货质检：登记 / 回查（WMS，写链路）
    FilePort       对象存储：质检报告等附件归档
    HandoffPort    转人工工单

三条契约（三套实现必须完全一致，`TOOL_GATEWAY=direct|mcp` 逐字段可比）
----------------------------------------------------------------------
1. **原样交出上游报文，不做字段映射。** 上游进来是 `matCode` / `refPrice`(分) /
   毫秒时间戳，出去还是它们；归一是 `integrations/normalize.py` 的显式一步。
   适配器里一旦开始「顺手改个名」，就等于在三套实现之间插进第四套口径，
   而它只在某条路径上生效 —— 这类不一致最难排查。
2. **单条 `get_*` 查不到返回 `None`，不抛异常。** 「这单不存在」是业务结论，
   该怎么措辞由上层决定。反过来，**写链路引用了不存在的实体**（下单引用
   查无此料的物料、收货引用查无此单的采购单）由实现抛 `*NotFound` —— 那是
   「这次写没法完成」，必须让上层看见。
3. **写操作不做去重。** 请求里带 `idempotency_key`，端口只管把它透传给上游
   （上游自己幂等是它的事）；「同一笔业务重复提交只执行一次」是
   `integrations/idempotency.IdempotencyGuard` 的职责。把去重也放进端口，
   演示时就分不清「是端口不重复」还是「幂等守卫拦住了」—— 恰好这个 demo
   就是靠后者的短路来演幂等的。

⚠️ 所有外部调用必须经由端口，禁止在节点里直接 new 客户端。子图只读状态、
装载层经工具函数取数，两条路径最终都收敛到这些端口上。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ==================== 读：主数据 ====================

@runtime_checkable
class MaterialPort(Protocol):
    """物料主数据（ERP 物料模块）。"""

    def list_materials(self) -> List[dict]:
        """全量物料（主数据同步用；装载链路每轮请求都会走一次）。"""
        ...

    def get_material(self, material_code: str) -> Optional[dict]:
        """按编码取单条；查不到返回 None（不抛）。"""
        ...

    def search_materials(self, keyword: str, limit: int = 20) -> List[dict]:
        """按关键词（编码或名称片段）搜索，供口语化指称落到具体物料。"""
        ...

    def list_substitutes(self, material_code: str) -> List[dict]:
        """该物料登记在册的替代料（返回替代料自身的主数据）。

        没登记替代料时返回空列表 —— 那是业务上的「没有」，不是故障。
        口径说明：图上还能算出「替代料有货吗」这类多跳事实（见 kg/query.py），
        这里只负责把主数据里登记的关系捞出来。
        """
        ...


@runtime_checkable
class SupplierPort(Protocol):
    """供应商主数据与报价（SRM）。"""

    def list_suppliers(self) -> List[dict]:
        """全量供应商（含履约指标：准时率 / 不良率 / 承诺交期）。"""
        ...

    def get_supplier(self, supplier_code: str) -> Optional[dict]:
        """按编码取单条；查不到返回 None。"""
        ...

    def list_supplier_quotes(self, material_code: str) -> List[dict]:
        """某物料各家供应商的报价（比价链路的输入）。

        只给报价事实（单价 / 起订量 / 有效期），**不在这里评分排名** ——
        打分口径属于 Agent 侧（sub_supplier）与图谱侧（kg/query.py）的实现，
        网关再放一份就会变成第三个会各自漂移的副本。
        """
        ...


@runtime_checkable
class WarehousePort(Protocol):
    """仓库主数据（WMS 基础档案）。"""

    def list_warehouses(self) -> List[dict]:
        """全量仓库。"""
        ...


# ==================== 读：库存 ====================

@runtime_checkable
class InventoryPort(Protocol):
    """库存快照（WMS）。三个视角对应三种真实查询：全量同步、按料看多仓、按仓盘点。"""

    def list_stock(self) -> List[dict]:
        """全量库存点（物料 × 仓库）。"""
        ...

    def list_stock_by_material(self, material_code: str) -> List[dict]:
        """某物料在各仓库的分布（库存子图 / 调拨判断的输入）。"""
        ...

    def list_stock_by_warehouse(self, warehouse_code: str) -> List[dict]:
        """某仓库里各物料（盘点视角）。"""
        ...


# ==================== 写：采购 / 收货 ====================

@runtime_checkable
class PurchasePort(Protocol):
    """采购订单（ERP 采购模块，写链路）。

    请求字段（内部口径）：request_id / material_code / qty / unit / unit_price /
    amount / supplier_code / target_warehouse / operator_id / idempotency_key。
    实现负责换算成上游报文字段并落单，返回**上游形状**的下单回执。
    """

    def create_purchase_order(self, request: dict) -> dict:
        """下达采购订单（写操作，必须携带 idempotency_key）。"""
        ...

    def get_purchase_order(self, po_no: str) -> Optional[dict]:
        """按采购单号回查；查不到返回 None。"""
        ...

    def list_purchase_orders(self) -> List[dict]:
        """全量采购订单（种子单 + 运行期下达的单，装载链路用）。"""
        ...


@runtime_checkable
class ReceiptPort(Protocol):
    """收货质检（WMS，写链路）。

    请求字段（内部口径）：po_no / material_code / qty_received / qty_defect /
    defect_type / disposition / warehouse_code / inspector / idempotency_key。
    应收数量、供应商等要素由实现从采购单反查，不要求调用方复述。
    """

    def submit_receipt(self, request: dict) -> dict:
        """登记收货并归档质检报告（写操作，必须携带 idempotency_key）。"""
        ...

    def get_receipt(self, receipt_no: str) -> Optional[dict]:
        """按收货单号回查；查不到返回 None。"""
        ...


# ==================== 附件 / 工单 ====================

@runtime_checkable
class FilePort(Protocol):
    """对象存储（OSS 风格）：把文本附件落成一个可回查的 URL。

    刻意**不进 MCP 网关**（`_PORT_TABLE` 里 mcp_cls 为 None）：附件写入是
    字节落盘、没有业务判断，网关的「薄包装 + 信封」对它没有增益；而且当前
    唯一的调用方在网关服务端内部（mock 收货落质检报告），再穿一次网关
    只是自己调自己。
    """

    def put_object(self, name: str, content: str,
                   content_type: str = "text/plain") -> dict:
        """写入一个文本对象，返回 {"object_key": ..., "url": ...}。"""
        ...


@runtime_checkable
class HandoffPort(Protocol):
    """转人工工单。

    请求字段：reason / summary / priority / thread_id / operator_id。
    """

    def create_ticket(self, request: dict) -> dict:
        """建单（写操作）。返回上游形状的工单回执（含工单号）。"""
        ...


# ==================== 异常 ====================

class IntegrationError(RuntimeError):
    """业务系统调用失败（网络、超时、上游报错，以及熔断快速失败）。"""


class MaterialNotFound(IntegrationError):
    """引用的物料不存在（写链路校验）。"""


class SupplierNotFound(IntegrationError):
    """引用的供应商不存在（写链路校验）。"""


class PurchaseOrderNotFound(IntegrationError):
    """引用的采购订单不存在（收货链路校验）。"""


class ReceiptNotFound(IntegrationError):
    """引用的收货单不存在。"""
