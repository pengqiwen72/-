# MCP 工具网关

把物料 / 供应商 / 仓库 / 库存 / 采购 / 收货能力以
[MCP](https://modelcontextprotocol.io) 协议暴露出去，说 stdio。
Agent 侧作为客户端连接它，用一个开关切过去：

```bash
cd scm_agent
python quickstart.py --gateway mcp          # 经网关
python quickstart.py --gateway direct       # 进程内直调（默认）
```

两条路径跑同一批场景，输出逐行一致 —— 差异只在 `TOOL_GATEWAY=direct|mcp` 这一个环境变量。

## 它解决什么

工具和 Agent 的变化节奏不一样：工具跟着业务系统走（接口改版、权限、灰度），
Agent 跟着提示词和编排走。中间隔一层标准协议之后：

- 工具可以单独部署、单独发版、单独限流，不必跟着 Agent 一起重启；
- 同一套工具能被任何 MCP 客户端复用，不用为每个接入方各写一份 SDK；
- 换第三方工具集只改 `MCP_SERVER_COMMAND` 指向谁，Agent 侧零改动。

在 demo 里它更直接的作用是**证明端口抽象是真的**：`integrations/ports.py` 那套 Protocol
现在有 mock / http / mcp 三套实现，节点代码一行没改。

## 工具清单（18 个）

| 域 | 工具 | 入参 | 说明 |
|---|---|---|---|
| 物料 | `list_materials` | — | 全量物料主数据（装载用） |
| 物料 | `search_materials` | `keyword`, `limit` | 按编码或名称片段搜索 |
| 物料 | `get_material` | `material_code` | 单条物料；不存在 → `not_found` |
| 物料 | `list_substitutes` | `material_code` | 登记在册的替代料 |
| 供应商 | `list_suppliers` | — | 全量供应商档案（含履约指标） |
| 供应商 | `get_supplier` | `supplier_code` | 单条供应商 |
| 供应商 | `list_supplier_quotes` | `material_code` | 该物料各家报价**事实** |
| 仓库 | `list_warehouses` | — | 全量仓库档案 |
| 库存 | `list_stock` | — | 全量库存快照（物料 × 仓库） |
| 库存 | `list_stock_by_material` | `material_code` | 某物料的多仓分布 |
| 库存 | `list_stock_by_warehouse` | `warehouse_code` | 某仓库的物料清单（盘点视角） |
| 采购 | `create_purchase_order` | `request`（含 `idempotency_key`） | 下达采购订单 |
| 采购 | `get_purchase_order` | `po_no` | 按单号回查（含运行期下的单） |
| 采购 | `list_purchase_orders` | — | 全量采购订单（装载用） |
| 收货 | `submit_receipt` | `request`（含 `idempotency_key`） | 办理收货登记 |
| 收货 | `get_receipt` | `receipt_no` | 回查收货记录（含质检报告地址） |
| 兜底 | `create_ticket` | `request` | 转人工工单 |
| 运维 | `ping` | — | 网关存活 + 背后指向 mock 还是真实系统 |

两处**故意不做**，值得单独说明：

* `list_supplier_quotes` 只给报价事实，**不做评分排名**；`list_stock_by_material`
  只给分布，**不给调拨建议**。排名口径与调拨判据属于业务决策（权重、安全线、
  高价告警），放在 Agent 侧（`sub_supplier` / `sub_inventory`）—— 网关一旦开始
  替业务做判断，切到真实系统时这套判断就跟着网关注入进了别人的系统。
* 网关不做字段映射（见下）。

## 返回信封

工具**永远正常返回**，业务失败装在信封里：

```json
{"ok": true,  "data": {"matCode": "M100023", "matName": "冷轧钢板 1.2mm", "...": "..."}}
{"ok": false, "error": "not_found", "message": "物料 M999999 不存在"}
```

`error` 取 `not_found` / `unavailable` / `invalid` / `internal`。

为什么不直接抛异常：MCP 协议层只有一个 `is_error`，它分不出

- 「这个物料没有登记替代料」—— 业务结论，要如实回答，不是故障；
- 「ERP 物料接口超时」—— 系统故障，要降级并转人工。

这两件事在业务链路里的处理路径完全不同，所以必须由信封把语义带过去，
`is_error` 只留给「工具实现本身崩了」。错误码到异常类的映射表只有一份
（`scm_agent/src/integrations/mcp/envelope.py`），服务端和客户端引用同一份，
避免出现「服务端抛 `MaterialNotFound`、客户端还原成泛化的 `IntegrationError`」
这种降级失真。

信封里的 `data` 是**上游原样报文**，网关不做字段映射：归一化留在 Agent 侧
（`integrations/normalize.py` 的字段别名表）。网关一旦开始改名换单位，就等于在
三套端口实现之间凭空插进第四套口径，而它只在 `TOOL_GATEWAY=mcp` 时生效 ——
这种「只在某条路径上存在的规则」最难排查。

## 直接跑

```bash
# 在仓库根目录
scm_agent/.venv/Scripts/python.exe -m mcp_server    # Windows
scm_agent/.venv/bin/python       -m mcp_server      # macOS / Linux
```

需要先装 SDK：`uv pip install 'mcp>=2.0'`（或 `pip install mcp`）。
没装也不影响默认的 `direct` 路径，会给出明确的安装提示而不是启动失败。

## 给别的 MCP 客户端用

因为说的是标准协议，任何 MCP 客户端都能直接接这套工具，不用改一行网关代码。
以 Claude Desktop 为例，`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "scm-business-tools": {
      "command": "<仓库路径>/scm_agent/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_server"],
      "cwd": "<仓库路径>"
    }
  }
}
```

> `command` 要指向**本项目 venv 里的解释器**：网关复用 `scm_agent` 的端口实现，
> 而它们依赖 langgraph 等包。真正的独立部署（网关自带数据库连接、不依赖 Agent 侧代码）
> 是把它拆出去的自然下一步，接口契约不用变。

## 环境变量

| 变量 | 作用 |
|---|---|
| `MCP_SERVER_COMMAND` | 覆盖启动命令，用来指向别的 MCP server |
| `MCP_CALL_TIMEOUT_SECONDS` | 单次工具调用超时（默认 10s），超时按「上游不可用」降级 |
| `INTEGRATION_MODE` | 网关背后接谁：`mock`（读种子）/ `http`（真实业务系统） |

`TOOL_GATEWAY` 会被**强制**设成 `direct`：网关自己绝不能再走一次网关。

> 还有一个只在 MCP 模式下才成立的坑：网关子进程的环境变量在**启动那一刻定格**，
> 之后改父进程 `os.environ` 送不进去。所以场景级故障注入（`MOCK_FAIL_ACTIONS`）
> 在进出场景时都要 `reset_ports()` 把网关关掉，让下一次调用拿新环境重启 ——
> 否则「注入故障」的场景会静默跑成正常路径，且不报任何错。
