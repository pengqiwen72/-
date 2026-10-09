"""
图谱的模式层：节点/边的种类，以及节点 ID 的拼法。

为什么把「边」限得这么死
------------------------
每种边只有一个含义、只连一类节点对。看起来比「关系表随便连」少了灵活性，
但换来的是：任何一条多跳路径都能用一句话讲清楚，而**讲不清楚的路径就是
不该建的路径**。

边只建「会被多跳遍历用到」的关系；单键属性留在节点属性里。比如采购单的
`target_warehouse` 就是一个属性，不建 PO -> WAREHOUSE 的边 —— 编码即事实，
不需要再用一条边把同一个字符串重复一遍。这条原则让图与种子 JSON 之间保持
「投影」关系：JSON 是唯一事实源，图从它推导，两边不会各自长出一套真相。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class NodeKind(str, Enum):
    MATERIAL = "material"
    SUPPLIER = "supplier"
    WAREHOUSE = "warehouse"
    STOCK_ITEM = "stock_item"      # 一条库存快照（物料 × 仓库），key 形如 M100023@WH01
    PO = "po"                      # 采购订单
    RECEIPT = "receipt"            # 收货单
    POLICY = "policy"              # 采购/质检策略规则


class EdgeKind(str, Enum):
    """有向边。每个种类的方向语义固定，见右侧注释。"""

    SUPPLIES = "SUPPLIES"          # 供应商 -> 物料（有报价才叫「供得了」）
    SUBSTITUTES = "SUBSTITUTES"    # 物料 -> 物料（替代料；互替需两边都登记）
    STOCKED_AT = "STOCKED_AT"      # 库存快照 -> 仓库
    IS_MATERIAL = "IS_MATERIAL"    # 库存快照/采购单/收货单 -> 物料（它说的是哪个物料）
    SOURCED_FROM = "SOURCED_FROM"  # 采购单 -> 供应商
    RECEIVED_AT = "RECEIVED_AT"    # 收货单 -> 仓库（收进了哪个仓）


class GraphStoreUnavailable(RuntimeError):
    """图后端不可用（缺驱动 / 连不上 / 未配置）。

    `get_graph_store()` 捕获它并回落到内存实现 —— 这不是错误路径，
    是「neo4j 可选」这个设计的一部分。
    """


def node_id(kind: NodeKind, key: str) -> str:
    """节点 ID 的唯一拼法：`<kind>:<key>`。

    带上 kind 前缀而不是裸编码，是为了让 ID 自解释 —— 日志、Cypher、
    报错信息里看到 `supplier:S007` 就知道是谁，不用回去查表。
    """
    return f"{kind.value}:{key}"


def stock_item_key(material_code: str, warehouse_code: str) -> str:
    """库存快照的 key：物料@仓库。@ 保证与两个编码都不会混淆。"""
    return f"{material_code}@{warehouse_code}"


@dataclass
class Node:
    node_id: str
    kind: NodeKind
    key: str
    props: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    src: str                              # 起点 node_id
    dst: str                              # 终点 node_id
    kind: EdgeKind
    props: Dict[str, Any] = field(default_factory=dict)
