"""
图存储的协议与工厂。

两种实现、一个开关
------------------
    GRAPH_STORE=memory   （默认）纯 Python 字典 + 邻接表，零依赖零配置
    GRAPH_STORE=neo4j    真实图库（需 `pip install neo4j` 并配置 NEO4J_URI/USER/PASSWORD）

镜像 `VECTOR_STORE` 的先例：默认实现保证 clone 下来就能跑，可选实现用来演示
「换真图库不改业务代码」—— 上层（`kg/query.py`、检索链路）只认本文件里的
`GraphStore` 协议，不知道背后是字典还是 neo4j。

neo4j 回落是**设计内行为**：缺驱动、URI 没配、连不上，全部走同一条路 ——
打一条 warning、换回内存实现。演示机器上没装图库是常态，不该因此跑不起来；
warning 保证「你以为在用 neo4j、其实在用内存」这件事不会被静默吞掉。

图数据从哪来
------------
`get_graph_store()` 在首次调用时从种子 JSON 投影出一张图（`store/seed_loader.py
::build_knowledge_graph`）。JSON 是唯一事实源，图是投影 —— 改种子即改图，
两侧不会各自维护一份真相。
"""

from __future__ import annotations

import os
from typing import List, Optional, Protocol, runtime_checkable

from kg.schema import Edge, EdgeKind, GraphStoreUnavailable, Node, NodeKind
from utils.agent_logger import get_logger

logger = get_logger("kg_store")


@runtime_checkable
class GraphStore(Protocol):
    """图存储的最小接口。查询层只依赖这七个方法。"""

    def upsert_node(self, kind: NodeKind, key: str, props: dict) -> str:
        """写入/更新一个节点，返回 node_id。"""
        ...

    def upsert_edge(self, src: str, dst: str, kind: EdgeKind,
                    props: Optional[dict] = None) -> None:
        """写入/更新一条边（同 src+dst+kind 视为同一条，属性覆盖）。"""
        ...

    def get_node(self, node_id: str) -> Optional[Node]:
        ...

    def nodes_of_kind(self, kind: NodeKind) -> List[Node]:
        """某类节点的全部实例，按 key 升序（确定性顺序，供索引与统计）。"""
        ...

    def neighbors(self, node_id: str, *, edge_kind: Optional[EdgeKind] = None,
                  direction: str = "both") -> List[Edge]:
        """取邻边。direction: "out" 出边 / "in" 入边 / "both" 两侧。

        默认两侧：知识图谱里的问题天然是双向的（「这家供应商还供哪些物料」
        走的就是 SUPPLIES 的反方向），要求每个调用方先想清楚方向只会出错。
        """
        ...

    def shortest_path(self, src: str, dst: str, *, max_hops: int = 4) -> Optional[List[str]]:
        """最短路径（不分方向），返回节点 ID 列表；不可达返回 None。"""
        ...

    def stats(self) -> dict:
        """节点/边计数，用于加载报告与诊断。"""
        ...


def graph_store_mode() -> str:
    return (os.getenv("GRAPH_STORE") or "memory").strip().lower()


_STORE: Optional[GraphStore] = None


def get_graph_store(*, force_rebuild: bool = False) -> GraphStore:
    """取图存储（进程级单例，首次调用时从种子投影建图）。"""
    global _STORE
    if _STORE is not None and not force_rebuild:
        return _STORE

    store = _new_store()
    from store.seed_loader import build_knowledge_graph

    stats = build_knowledge_graph(store)
    logger.info(
        f"知识图谱构建完成（{graph_store_mode()}）：{stats['node']} 节点 / "
        f"{stats['edge']} 边"
    )
    _STORE = store
    return store


def reset_graph_store() -> None:
    """清空单例（测试、场景级环境切换用）。"""
    global _STORE
    _STORE = None


def _new_store() -> GraphStore:
    if graph_store_mode() in {"neo4j", "remote"}:
        from kg.neo4j_store import Neo4jGraphStore

        try:
            logger.info("图存储：neo4j")
            return Neo4jGraphStore()
        except GraphStoreUnavailable as e:
            logger.warning(f"GRAPH_STORE=neo4j 不可用（{e}），回退内存图存储")

    from kg.memory_store import MemoryGraphStore

    return MemoryGraphStore()
