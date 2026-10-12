# scm_agent —— 智能体核心

这个模块是整套系统的核心：LangGraph 主图 + 5 条业务子图（智能问答 / 采购申请 /
收货验收 / 库存管理 / 供应商管理）+ 1 条兜底子图，RAG 双通道检索（制度文档 ×
物料-供应商-仓库图谱）、业务系统集成（端口／适配器）、策略引擎与幂等、
FastAPI 服务与演示工作台页面。

**只想看效果的话，不用从这个模块入手** —— 仓库根目录的 `quickstart.py` 离线就能
跑通全部链路：

```bash
uv sync --project scm_agent
uv run --project scm_agent python quickstart.py
```

## 起服务模式

```bash
./run_scm_agent        # langgraph dev，:2024；自动装配 webapp 的 /scm/* 路由
```

启动后：

* 演示工作台：<http://127.0.0.1:2024/scm/workbench/>（账号取 `.env` 的 `DEMO_USER_*`）
* LangGraph 平台接口：`:2024`（`/ok` 可直接探活）

> `run_scm_agent` 里刻意用 `python -m langgraph_cli dev` 而不是 `uv run langgraph dev` ——
> 后者在 Windows/Git-Bash 下会撞 uv 的 trampoline 报 "failed to canonicalize script path"。

不用浏览器也能验收 SSE 与 HITL 全流程：

```bash
cd scm_agent && set -a && . ./.env && set +a    # 取 DEMO_USER_*（未配置时用内置默认值）

TOKEN=$(curl -s -X POST localhost:2024/scm/get_token -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$DEMO_USER_ID\",\"password\":\"$DEMO_USER_PASSWORD\"}" | \
  python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# ① 菜单 id 直达（不调模型；入参刻意用 ASCII，绕开终端编码差异）
curl -sN -X POST localhost:2024/scm/chat/stream -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"task_input":"inventory_query"}'
# -> event: open / progress ×N / done（该链路不需要人工确认）

# ② HITL：这一条要的是自然语言（要素提取从原文里抽），所以入参是中文 ——
#    控制台得能过 UTF-8（Git Bash 或 PYTHONUTF8=1），否则请直接在工作台上点。
curl -sN -X POST localhost:2024/scm/chat/stream -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d '{"task_input":"给 WH01 申购 8000kg 冷轧钢板 1.2mm，指定 S012，产线急用"}'
# -> open / progress ×N / interrupt（审批卡片里带 thread_id 与方案要素）/ todo

# ③ 审批：decision 取 ok | discard | recreate，必须回传同一个 thread_id；
#    recreate 时可选 patch —— 采购只认 supplier_code / qty / target_warehouse
curl -sN -X POST localhost:2024/scm/chat/resume -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"thread_id":"<上一步的>","decision":"recreate","patch":{"supplier_code":"S001"}}'
# -> progress ×N / done（task_result_code=0，改判后免审批直接下单）
```

可用的菜单 id 就是 `scm_node.py` 的 `NodeName`：`material_qa` / `purchase_request` /
`goods_receipt` / `inventory_query` / `supplier_query` / `no_clear_intention`。
事件与字段的完整说明在各路由的 docstring（`src/webapp/chat_routes.py`）。

## src 目录说明

| 目录 | 内容 |
|---|---|
| `graphs/scm_graph/` | 主图（统一调度）与 6 个子图，每个子图是 `graph / state / node` 三件套 |
| `kg/` | 物料-供应商-仓库图谱：schema / 内存实现（可切 neo4j）/ 多跳查询 / 实体链接，与 `rag/` 平行的一层 |
| `rag/` | 解析 → 分块 → 向量化 → 混合召回（文档 × 图谱第三路）→ 重排 → 置信度 → 出处；默认纯 Python，`VECTOR_STORE=milvus` 可切向量库 |
| `llm/` | 提示词与模型调用；`llm_setting.py` 是真实／离线模型的唯一切换点，`offline/` 是离线合成器 |
| `integrations/` | `ports.py` 定义业务接口，`mock/` 与 `http/` 分别实现；`resilience.py` 熔断、`normalize.py` 字段归一、`policy.py` 策略引擎、`idempotency.py` 幂等、`mcp/` 工具网关桥 |
| `webapp/` | FastAPI：SSE 对话接口、JWT、演示工作台静态页（`static/`） |
| `demo/` | 场景表与进程内 runner —— 与 webapp 共用同一套进度事件 |
| `store/` | Store 访问（`store_provider.py` 提供脱离平台运行时可用进程内实现） |
| `models/` | 跨检查点的 pydantic 模型（状态里不放 dataclass） |
| `observability/` | 进度事件（`progress.py`）、降级分级（`degrade.py`）与 Langfuse 埋点 |
| `data/` | `seed/` 虚构业务数据、`knowledge/` RAG 语料、`index/` 生成的检索索引 |

架构与模块间契约见 [`docs/architecture.md`](../docs/architecture.md)；
「看着能简化实则不能动」的地方在各文件 docstring 里。

## 配置

复制 `.env.example` 为 `.env`。默认值刻意全部指向离线模式，**不填任何密钥也能跑通**。
接真实模型需要 `LLM_MODE=live` 与四项模型配置。

`.env*` 的注释一律用 ASCII（英文）：`langgraph dev` 通过 python-dotenv 读 `.env` 时
在中文 Windows 上会按 GBK 解码，UTF-8 中文注释会让它直接抛 `UnicodeDecodeError`。

服务模式下还有一条硬约束：**别裸调 `load_dotenv()`，也别写 `Path(__file__).resolve()`**。
`langgraph dev` 的 blockbuster 守卫会拦 `os.getcwd()`（只在事件循环线程里生效），
而这两个写法内部都在调它 —— 模块只要是「首次请求才被 import」的（演示工作台首次
请求才建图），整条 SSE 请求就直接 500。所以：`.env` 统一走 `utils/env_loader.py`，
模块级路径常量统一写词法的 `Path(__file__).parents[n]`。实测放行/拦截清单见
`utils/env_loader.py` 的模块说明。

## 依赖

`uv sync` 装基础依赖（含 MCP SDK）。`pyproject.toml` 里有一条
`[tool.uv] override-dependencies`，理由与实测结论写在该文件注释里。

## 自建平台部署（可选，本 demo 不依赖）

`scm.dockerfile` 与 `docker-compose.yml` 是 LangGraph 平台自建部署用的（前者由
`langgraph build` 生成）。`requirements.txt` **由锁文件机械导出**，保证不会与
`pyproject.toml` 漂移 —— 改过依赖后重新生成：

```bash
uv export --no-dev --no-emit-project --no-hashes --format requirements-txt -o requirements.txt
```

## 测试

```bash
uv run --project . python -m pytest tests -q          # 后端
node tests/frontend/timeline_check.mjs                 # 前端时间线折叠逻辑
```
