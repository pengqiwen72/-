"""
知识图谱层：实体链接 + 图存储 + 多跳查询。

在整体分层里的位置
------------------
    rag/        文档检索（TF-IDF + BM25 + 重排）
    kg/         图谱检索（实体链接 + 多跳路径）      <- 本包
    graphs/     业务子图（LangGraph）
    webapp/     HTTP 外壳

**红线：kg 不允许 import rag / graphs / webapp。** 依赖方向是单向的 ——
rag 可以 import kg（检索层把两路通道融合在一起），kg 反过来一概不行。
理由不是洁癖：图查询是「种子 JSON → 图事实」的纯计算，一旦它开始依赖
rag 的重排或子图的状态模型，它就无法在会话、HTTP、图运行时之外被单独
测试和复用了。图事实的类型由 kg 自己定义（`query.py::GraphChunk`），
跨层转换放在检索层一处完成。

模块导览
--------
    schema.py        节点/边种类、node_id 拼法、GraphStoreUnavailable
    store.py         GraphStore 协议 + 工厂（memory / neo4j 开关与回落）
    memory_store.py  默认实现：字典 + 邻接表
    neo4j_store.py   可选实现（缺驱动/连不上 -> warning 回落）
    link.py          文本 -> 节点编码（正则直命中 / 名称倒排 / bigram 兜底）
    query.py         三个多跳查询 + `retrieve()` 通道入口（含 L1 降级）

图数据是 `data/seed/*.json` 的**投影**（`store/seed_loader.py::build_knowledge_graph`），
JSON 是唯一事实源；改种子即改图。
"""
