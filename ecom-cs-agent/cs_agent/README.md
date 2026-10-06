# cs_agent —— 智能体核心

这个模块是整套系统的核心：LangGraph 主图 + 10 条业务子图、RAG 检索、业务系统集成
（端口／适配器）、政策引擎与幂等、FastAPI 服务与坐席工作台页面。

**只想看效果的话，不用从这个模块入手** —— 仓库根目录的 `quickstart.py` 离线就能
跑通全部链路：

```bash
uv sync --project cs_agent
uv run --project cs_agent python quickstart.py
```

## 起服务模式

```bash
./run_cs_agent        # langgraph dev，:2024；自动装配 webapp 的 /cs/* 路由
```

启动后：

* 坐席工作台：<http://127.0.0.1:2024/cs/workbench/>（账号取 `.env` 的 `DEMO_USER_*`）
* LangGraph 平台接口：`:2024`（`/ok` 可直接探活）

> `run_cs_agent` 里刻意用 `python -m langgraph_cli dev` 而不是 `uv run langgraph dev` ——
> 后者在 Windows/Git-Bash 下会撞 uv 的 trampoline 报 "failed to canonicalize script path"。

不用浏览器也能验收 SSE 与 HITL 全流程（输入刻意用 ASCII 菜单 id，绕开终端编码差异）：

```bash
cd cs_agent && set -a && . ./.env && set +a    # 取 DEMO_USER_*（未配置时用内置默认值）

TOKEN=$(curl -s -X POST localhost:2024/cs/get_token -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$DEMO_USER_ID\",\"password\":\"$DEMO_USER_PASSWORD\"}" | \
  python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# ① 发起：菜单 id 命中枚举（不调模型）直达标签链；打完 progress 停在 interrupt
curl -sN -X POST localhost:2024/cs/chat/stream -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"task_input":"tag_suggestion"}'
# -> event: open / progress ×N / interrupt（审批卡片里带 thread_id）

# ② 审批：decision 取 ok | discard | recreate，必须回传同一个 thread_id
curl -sN -X POST localhost:2024/cs/chat/resume -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"thread_id":"<上一步的>","decision":"ok"}'
# -> progress ×N / done（task_result_code=0）
```

可用的菜单 id 就是 `cs_node.py` 的 `NodeName`：`product_qa` / `order_query` /
`after_sale` / `weekly_report` / `tag_suggestion` / `profile_suggestion` /
`schedule_suggestion` / `chat_suggestion` / `kf_chat_suggestion`。
事件与字段的完整说明在各路由的 docstring（`src/webapp/chat_routes.py`）。

## src 目录说明

| 目录 | 内容 |
|---|---|
| `graphs/cs_graph/` | 主图（统一调度）与 10 个业务子图，每个子图是 `graph / state / node` 三件套 |
| `rag/` | 解析 → 分块 → 向量化 → 混合召回 → 重排 → 置信度 → 出处；默认纯 Python，`VECTOR_STORE=milvus` 可切向量库 |
| `llm/` | 提示词与模型调用；`llm_setting.py` 是真实／离线模型的唯一切换点，`offline/` 是离线合成器 |
| `integrations/` | `ports.py` 定义业务接口，`mock/` 与 `http/` 分别实现；`normalize.py` 字段归一、`policy.py` 政策引擎、`idempotency.py` 幂等、`mcp/` 工具网关桥 |
| `webapp/` | FastAPI：SSE 对话接口、JWT、坐席工作台静态页（`static/`） |
| `demo/` | 场景表与进程内 runner —— 与 webapp 共用同一套进度事件 |
| `store/` | Store 访问（`store_provider.py` 提供脱离平台运行时可用进程内实现） |
| `models/` | 跨检查点的 pydantic 模型（状态里不放 dataclass） |
| `observability/` | 进度事件（`progress.py`）与 Langfuse 埋点 |
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
而这两个写法内部都在调它 —— 模块只要是「首次请求才被 import」的（坐席工作台首次
请求才建图），整条 SSE 请求就直接 500。所以：`.env` 统一走 `utils/env_loader.py`，
模块级路径常量统一写词法的 `Path(__file__).parents[n]`。实测放行/拦截清单见
`utils/env_loader.py` 的模块说明。

## 依赖

`uv sync` 装基础依赖（含 MCP SDK）。`pyproject.toml` 里有一条
`[tool.uv] override-dependencies`，理由与实测结论写在该文件注释里。

## 自建平台部署（可选，本 demo 不依赖）

`cs.dockerfile` 与 `docker-compose.yml` 是 LangGraph 平台自建部署用的（前者由
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
