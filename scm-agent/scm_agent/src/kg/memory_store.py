"""
内存图存储：字典 + 正反向邻接表。

规模前提是**种子数据**（数百节点、数百边），所以一切从简：
  * 节点挂在 `dict[node_id] -> Node`；
  * 边同时挂进 `_out[src]` 与 `_in[dst]`，两侧取邻边都是 O(1) 定位；
  * 遍历顺序 = 插入顺序 = 种子 JSON 里的书写顺序。这一点是有意保的：
    确定性输出是演示与测试的前提，顺序一旦随哈希漂移，同一次查询两次结果
    就会不同（`Python` 的 dict 保序恰好免费给了我们这个性质）。
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Callable, Dict, List, Optional

from kg.schema import Edge, EdgeKind, Node, NodeKind, node_id


def _upsert(bucket: List[Edge], edge: Edge, match: Callable[[Edge], bool]) -> None:
    for i, old in enumerate(bucket):
        if match(old):
            bucket[i] = edge
            return
    bucket.append(edge)


class MemoryGraphStore:
    """`GraphStore` 协议的默认实现。"""

    def __init__(self) -> None:
        self._nodes: Dict[str, Node] = {}
        self._out: Dict[str, List[Edge]] = defaultdict(list)
        self._in: Dict[str, List[Edge]] = defaultdict(list)

    # ---------- 写入 ----------

    def upsert_node(self, kind: NodeKind, key: str, props: dict) -> str:
        nid = node_id(kind, key)
        existing = self._nodes.get(nid)
        if existing is None:
            self._nodes[nid] = Node(node_id=nid, kind=kind, key=key, props=dict(props))
        else:
            existing.props.update(props)
        return nid

    def upsert_edge(self, src: str, dst: str, kind: EdgeKind,
                    props: Optional[dict] = None) -> None:
        edge = Edge(src=src, dst=dst, kind=kind, props=dict(props or {}))
        # 两侧桶里存的是同一个 Edge 对象，命中已有边时两边一起替换，保持一致
        _upsert(self._out.setdefault(src, []), edge, lambda e: e.kind == kind and e.dst == dst)
        _upsert(self._in.setdefault(dst, []), edge, lambda e: e.kind == kind and e.src == src)

    # ---------- 读取 ----------

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def nodes_of_kind(self, kind: NodeKind) -> List[Node]:
        return sorted(
            (n for n in self._nodes.values() if n.kind == kind),
            key=lambda n: n.key,
        )

    def neighbors(self, node_id: str, *, edge_kind: Optional[EdgeKind] = None,
                  direction: str = "both") -> List[Edge]:
        if direction == "out":
            edges = list(self._out.get(node_id, []))
        elif direction == "in":
            edges = list(self._in.get(node_id, []))
        else:
            edges = list(self._out.get(node_id, [])) + list(self._in.get(node_id, []))
        if edge_kind is not None:
            edges = [e for e in edges if e.kind == edge_kind]
        return edges

    def shortest_path(self, src: str, dst: str, *, max_hops: int = 4) -> Optional[List[str]]:
        if src not in self._nodes or dst not in self._nodes:
            return None
        if src == dst:
            return [src]

        # 不分方向的 BFS：图是「知识」而不是「流程」，路径没有通行方向
        seen = {src}
        queue: deque = deque([(src, [src])])
        while queue:
            current, path = queue.popleft()
            if len(path) - 1 >= max_hops:
                continue
            for edge in self.neighbors(current):
                nxt = edge.dst if edge.src == current else edge.src
                if nxt in seen:
                    continue
                if nxt == dst:
                    return path + [nxt]
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
        return None

    def stats(self) -> dict:
        by_kind: Dict[str, int] = defaultdict(int)
        for n in self._nodes.values():
            by_kind[n.kind.value] += 1
        by_edge: Dict[str, int] = defaultdict(int)
        for edges in self._out.values():
            for e in edges:
                by_edge[e.kind.value] += 1
        return {
            "backend": "memory",
            "node": len(self._nodes),
            "edge": sum(by_edge.values()),
            "by_kind": dict(by_kind),
            "by_edge": dict(by_edge),
        }
