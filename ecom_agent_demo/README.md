# 电商智能客服与运营助手(最小可运行 Demo)

「电商智能客服与运营助手」的最小可运行版本,用于演示核心设计。核心展示四点:

1. **统一调度 + 业务链路**的工作流编排(替代"单 Agent 挂全部工具")
2. **事实性数据必须来自 API、模型仅转述**的防幻觉硬约束
3. **高风险操作走 HITL 人工确认 + 幂等控制**的合规设计
4. **数值计算交给代码、语义总结交给模型**的数据处理原则

---

## 快速运行

```bash
# 方式一:使用 uv(推荐)
uv venv && uv pip install -r requirements.txt
python demo.py

# 方式二:使用 pip
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
python demo.py
```

**无需任何 API Key 即可完整跑通全部 6 个场景**(未配置时自动使用内置规则实现)。
若需接入真实模型:

```bash
cp .env.example .env      # 填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
```

---

## 目录结构

```
ecom_agent_demo/
├── demo.py                 # 演示入口:6 个场景
├── requirements.txt        # 仅依赖 langgraph + pydantic
└── app/
    ├── graph.py            # LangGraph 工作流编排(核心)
    ├── state.py            # 统一状态模型 + 自定义 reducer
    ├── models.py           # Pydantic 结构化模型与安全解析
    ├── llm.py              # 可插拔 LLM(在线模型 / 离线规则双模)
    ├── knowledge.py        # RAG 检索 + 置信度阈值(可替换为 Milvus)
    ├── tools.py            # 多路业务 API 编排 + 字段归一 + 幂等
    └── weekly.py           # 多平台数据清洗汇总 + 同环比计算
```

---

## 工作流结构

```
                    ┌──────────────────┐
  用户请求 ────────► │   reset_state    │  清空上一轮中间结果(reducer 空值重置)
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │   route_intent   │  统一调度层:枚举精确匹配 + 语义识别兜底
                    └────────┬─────────┘
              ┌──────────────┼──────────────┬──────────────┐
              ▼              ▼              ▼              ▼
      ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
      │  qa_chain   │ │ order_chain │ │after_sale_  │ │weekly_chain │
      │ 智能问答    │ │ 订单查询    │ │chain 售后   │ │ 数据周报    │
      │ RAG+阈值    │ │ 多API编排   │ │ 政策+HITL   │ │ 代码计算    │
      │ 转人工兜底  │ │ 模型仅转述  │ │ +幂等       │ │ 模型摘要    │
      └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
             │               │               │(高风险)        │
             │               │               ▼               │
             │               │        ┌─────────────┐        │
             │               │        │human_approval│ interrupt 挂起
             │               │        │  人工确认    │ Command(resume) 恢复
             │               │        └──────┬──────┘        │
             └───────────────┴───────────────┴───────────────┘
                             ▼
                    ┌──────────────────┐
                    │    finalize      │  task_result 统一输出
                    └──────────────────┘
```

---

## 6 个演示场景

| # | 场景 | 展示的能力 |
|---|---|---|
| 1 | 商品咨询"保温杯容量和材质" | RAG 检索命中,回答强制携带知识出处 |
| 2 | 知识库未覆盖的问题 | 置信度阈值判定 -> 不强行作答,自动转人工兜底 |
| 3 | "订单 SO20260618001 到哪了" | 编排订单/物流/库存 3 路 API,字段归一,模型仅转述 |
| 4 | "订单 SO20260620007 我要退款" | 政策匹配识别高风险 -> interrupt 挂起 -> 审批 -> resume 执行 |
| 5 | 同一笔退款重复提交 | 幂等控制(订单号 + 操作类型),重复提交返回首次结果 |
| 6 | "帮我出本周数据周报" | 跨平台数据清洗 + 同环比计算由代码完成,模型仅写摘要 |

---

## 关键设计说明

### 1. 自定义 reducer:节点结果增量合并
`app/state.py` 中 `reducer_node_result`:
- 传入空列表 -> **置空**(每轮请求重置执行记录,避免多轮会话结果串台)
- 传入非空 -> **追加**(多条链路结果不被覆盖)

LangGraph 默认是"覆盖"语义,多节点协作会丢数据;自定义 reducer 明确合并规则。

### 2. 事实性数据硬约束(防幻觉的架构手段)
`order_chain` 中:订单号用正则从问题中**确定性抽取**,数据全部来自业务 API,
模型只做语言组织(`paraphrase_order`),提示词明确"严禁修改或新增任何数值"。
—— 在架构层面约束模型的生成范围,而非仅靠提示词提醒模型"不要编造"。

### 3. HITL 与幂等
- `human_approval` 节点调用 `interrupt()` 挂起工作流,State 由 checkpointer 持久化,
  主管审批后通过 `Command(resume="approve")` 从挂起点恢复,而非整图重跑
- `IdempotencyStore` 以「订单号 + 操作类型」为唯一键,重复请求直接返回首次执行结果

### 4. 计算与生成分离
`weekly.py` 完成字段映射、缺失值清洗(前值填充)、跨平台汇总与同环比计算;
LLM 只把统计结果写成一段摘要。**避免大模型参与数值计算导致数据失真。**

---

## 与生产实现的差异

| 组件 | Demo 实现 | 生产实现 |
|---|---|---|
| 向量检索 | n-gram 余弦相似度(零依赖) | Milvus + Embedding + Rerank |
| LLM | 在线模型 / 离线规则双模 | Qwen(OpenAI 兼容接口) |
| 持久化 | MemorySaver(进程内存) | Checkpointer + Store(Postgres) |
| 业务 API | 内存字典模拟 | 真实业务系统 HTTP 接口 |
| 服务化 | 直接脚本运行 | FastAPI + SSE + JWT |

> 注:运行时会输出 `Deserializing unregistered type` 提示,这是 LangGraph 对
> checkpoint 中自定义 Pydantic 类型的注册提醒,**不影响运行**;生产环境通过
> `allowed_msgpack_modules` 显式注册即可消除。
