"""
Neo4j 图存储（可选后端，`GRAPH_STORE=neo4j` 时启用）。

这个文件在 demo 里的角色是**证明分层成立**：`kg/query.py` 的三条多跳查询
不知道自己跑在字典上还是跑在 Neo4j 上，换后端只改环境变量。

几条实现纪律
------------
* `import neo4j` 放在函数里 —— 驱动是可选的，装了才 import；没装时
  构造器抛 `GraphStoreUnavailable`，由 `get_graph_store()` 打 warning 回落内存。
* 关系只用一个类型 `:REL`，用属性 `edge_kind` 区分六种语义。否则 Cypher 里
  要按边类型拼字符串（`[:{kind}]`），那是在查询模板里做字符串插值，
  既难维护又贴着注入的形状；属性过滤是参数化查询，干净且可索引。
* 所有写入走 `MERGE` + `SET n += $props`，与内存实现的 upsert 语义对齐
  （重复投影同一份种子 = 覆盖同样的键，不产生重复节点）。
"""

from __future__ import annotations

import os
from typing import List, Optional

from kg.schema import Edge, EdgeKind, GraphStoreUnavailable, Node, NodeKind, node_id
from utils.agent_logger import get_logger

logger = get_logger("kg_neo4j")

#: 所有图节点共用的标签；种类存在 `kind` 属性里（同 `node_id` 前缀）
_LABEL = "ScmNode"
#: 所有关系共用的类型；语义存在 `edge_kind` 属性里（见模块文档）
_REL = "REL"


class Neo4jGraphStore:
    """基于 neo4j 驱动的图存储实现。"""

    def __init__(self, uri: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None) -> None:
        try:
            import neo4j  # noqa: F401
            from neo4j import GraphDatabase
        except ImportError as e:
            raise GraphStoreUnavailable(
                "未安装 neo4j 驱动（pip install neo4j）"
            ) from e

        uri = uri or (os.getenv("NEO4J_URI") or "").strip()
        if not uri:
            raise GraphStoreUnavailable("NEO4J_URI 未配置")
        user = user or (os.getenv("NEO4J_USER") or "neo4j")
        password = password or (os.getenv("NEO4J_PASSWORD") or "")

        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
            self._driver.verify_connectivity()
        except Exception as e:
            raise GraphStoreUnavailable(f"连接 Neo4j 失败：{e}") from e

        self._ensure_constraint()

    def _ensure_constraint(self) -> None:
        try:
            with self._driver.session() as session:
                session.run(
                    f"CREATE CONSTRAINT scm_node_id IF NOT EXISTS "
                    f"FOR (n:{_LABEL}) REQUIRE n.node_id IS UNIQUE"
                )
        except Exception as e:  # pragma: no cover - 权限不足等环境问题
            logger.warning(f"创建唯一约束失败（不影响使用，只是少一层保护）：{e}")

    # ---------- 写入 ----------

    def upsert_node(self, kind: NodeKind, key: str, props: dict) -> str:
        nid = node_id(kind, key)
        clean = _clean(props)
        with self._driver.session() as session:
            session.run(
                f"MERGE (n:{_LABEL} {{node_id: $id}}) "
                f"SET n.kind = $kind, n.key = $key, n += $props",
                id=nid, kind=kind.value, key=key, props=clean,
            )
        return nid

    def upsert_edge(self, src: str, dst: str, kind: EdgeKind,
                    props: Optional[dict] = None) -> None:
        clean = _clean(props or {})
        with self._driver.session() as session:
            session.run(
                f"MATCH (a:{_LABEL} {{node_id: $src}}), (b:{_LABEL} {{node_id: $dst}}) "
                f"MERGE (a)-[r:{_REL} {{edge_kind: $kind}}]->(b) "
                f"SET r += $props",
                src=src, dst=dst, kind=kind.value, props=clean,
            )

    # ---------- 读取 ----------

    def get_node(self, node_id: str) -> Optional[Node]:
        with self._driver.session() as session:
            record = session.run(
                f"MATCH (n:{_LABEL} {{node_id: $id}}) RETURN n", id=node_id
            ).single()
        return _to_node(record["n"]) if record else None

    def nodes_of_kind(self, kind: NodeKind) -> List[Node]:
        with self._driver.session() as session:
            records = session.run(
                f"MATCH (n:{_LABEL} {{kind: $kind}}) RETURN n ORDER BY n.key",
                kind=kind.value,
            )
            return [_to_node(r["n"]) for r in records]

    def neighbors(self, node_id: str, *, edge_kind: Optional[EdgeKind] = None,
                  direction: str = "both") -> List[Edge]:
        # 三种方向各一条模板，显式写开：拼 Cypher 的箭头比任何「聪明」的
        # 字符串替换都可靠（见模块文档：不做字符串插值式的边类型拼接）
        arrows = {
            "out": "-[r:{rel}]->",
            "in": "<-[r:{rel}]-",
            "both": "-[r:{rel}]-",
        }[direction].format(rel=_REL)
        cypher = (
            f"MATCH (a:{_LABEL} {{node_id: $id}}){arrows}(b) "
            f"WHERE $edge_kind IS NULL OR r.edge_kind = $edge_kind "
            f"RETURN r, a, b"
        )

        with self._driver.session() as session:
            records = session.run(cypher, id=node_id,
                                  edge_kind=edge_kind.value if edge_kind else None)
            out: List[Edge] = []
            for rec in records:
                rel, a, b = rec["r"], rec["a"], rec["b"]
                # 入边方向与匹配模式相反：a 是锚点但边的起点是 b。
                # 不修正的话调用方会拿到方向翻转的边，语义静默出错。
                if direction == "in":
                    src, dst = b["node_id"], a["node_id"]
                else:
                    src, dst = a["node_id"], b["node_id"]
                props = dict(rel)
                props.pop("edge_kind", None)
                out.append(Edge(
                    src=src, dst=dst, kind=EdgeKind(rel["edge_kind"]), props=props,
                ))
            return out

    def shortest_path(self, src: str, dst: str, *, max_hops: int = 4) -> Optional[List[str]]:
        with self._driver.session() as session:
            record = session.run(
                f"MATCH (a:{_LABEL} {{node_id: $src}}), (b:{_LABEL} {{node_id: $dst}}), "
                f"p = shortestPath((a)-[:{_REL}*..{int(max_hops)}]-(b)) "
                f"RETURN [n IN nodes(p) | n.node_id] AS ids",
                src=src, dst=dst,
            ).single()
        return list(record["ids"]) if record else None

    def stats(self) -> dict:
        with self._driver.session() as session:
            nodes = session.run(f"MATCH (n:{_LABEL}) RETURN count(n) AS c").single()["c"]
            edges = session.run(f"MATCH ()-[r:{_REL}]->() RETURN count(r) AS c").single()["c"]
        return {"backend": "neo4j", "node": nodes, "edge": edges}


def _clean(props: dict) -> dict:
    """剥掉 None —— Cypher 的 `SET n += $props` 见到 None 会写 null 属性，
    而「字段缺省」与「字段为 null」在下游读起来是两种语义。"""
    return {k: v for k, v in props.items() if v is not None}


def _to_node(raw) -> Node:
    props = dict(raw)
    kind = NodeKind(props.pop("kind"))
    key = str(props.pop("key", ""))
    node = Node(node_id=props.pop("node_id", ""), kind=kind, key=key, props=props)
    return node
