# scm_client —— 客户端 SDK 与端到端脚本

这一组脚本用于**打真实运行中的平台服务**（不是离线 quickstart）：灌演示数据、
跑一轮对话、直接读写 Store 做排查。它们既是 SDK 用法示例，也是服务模式的验收手段。

## 前置

先起平台服务，再跑这里的脚本；否则会看到连不上 `127.0.0.1:2024` 的报错：

```bash
cd ../scm_agent && ./run_scm_agent
```

脚本之间是**同目录相互 import**（`from scm_store_api import ...`），所以要走
`sys.path` 的话得进到脚本所在目录再跑 —— 不要用 `python -m store_client.xxx`：

```bash
cd store_client && uv run python scm_demo_init.py
cd agent_client && uv run python scm_agent_test.py test
```

## 脚本

| 脚本 | 作用 |
|---|---|
| `store_client/scm_demo_init.py` | 把演示数据灌进平台 Store（客户、标签体系、员工等） |
| `store_client/scm_store_test.py` | Store 读接口的排查工具：`show_all` / `show_namespace` / `test_cleanup` |
| `agent_client/scm_agent_api.py` | 平台接口的客户端封装（创建 assistant / thread、发起与恢复对话） |
| `agent_client/scm_agent_test.py` | 端到端脚本，带子命令：`test`（跑一轮）、`resume_confirmed <决策>`、`delete_thread <id>`、`delete_all_threads` |

## 配置（`.env`）

| 变量 | 说明 |
|---|---|
| `SCM_SERVER_URL` | 平台服务地址，默认 `http://127.0.0.1:2024` |
| `SCM_USER_ID` / `SCM_USER_PASSWORD` | 换令牌用，POST `{SCM_SERVER_URL}/scm/get_token` 校验的就是这一对；**必须与 `scm_agent` 的 `DEMO_USER_ID` / `DEMO_USER_PASSWORD` 一致** |

缺变量时 `scm_demo_init.py` 会明确报出缺哪一个并退出（不会静默跑成空操作）。

> `store_client/scm_store_api.py` 是 `scm_admin/scm_store_api/` 那份的超集 ——
> 这里多出写接口（`upsert_external_user`、`upsert_tags_setting` 等）供灌数据用。
