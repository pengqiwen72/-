# MCP 工具网关

把订单 / 物流 / 库存 / 售后能力以 [MCP](https://modelcontextprotocol.io) 协议暴露出去，
说 stdio。Agent 侧作为客户端连接它，用一个开关切过去：

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

## 工具清单

| 工具 | 入参 | 说明 |
|---|---|---|
| `get_order` | `order_id` | 订单主数据 |
| `list_orders_by_customer` | `customer_id`, `limit` | 客户名下订单（按时间倒序） |
| `list_orders_by_range` | `start`, `end`, `limit` | 按下单区间取数，供统计与周报 |
| `get_logistics` | `order_id` | 物流轨迹 |
| `get_stock` | `sku_ids`, `warehouse` | 批量库存 |
| `submit_refund` | `request`（含 `idempotency_key`） | 发起退款 |
| `get_refund` | `refund_id` | 回查退款单 |
| `create_ticket` | `request` | 转人工工单 |

## 返回信封

工具**永远正常返回**，业务失败装在信封里：

```json
{"ok": true,  "data": {"orderNo": "SO20260318002", "tradeStatus": "30", "...": "..."}}
{"ok": false, "error": "not_found", "message": "订单 SO20260318002 暂无物流轨迹"}
```

`error` 取 `not_found` / `unavailable` / `invalid` / `internal`。

为什么不直接抛异常：MCP 协议层只有一个 `is_error`，它分不出

- 「这单还没发货，所以查不到物流轨迹」—— 业务结论，要如实回答客户；
- 「快递查询接口超时」—— 系统故障，要降级并转人工。

这两件事在客服链路里的处理路径完全不同，所以必须由信封把语义带过去，
`is_error` 只留给「工具实现本身崩了」。错误码到异常类的映射表只有一份
（`scm_agent/src/integrations/mcp/envelope.py`），服务端和客户端引用同一份，
避免出现「服务端抛 `LogisticsNotFound`、客户端还原成泛化的 `IntegrationError`」
这种降级失真。

信封里的 `data` 是**上游原样报文**，网关不做字段映射：归一化留在 Agent 侧。
网关一旦开始改名换单位，就等于在三套端口实现之间凭空插进第四套口径，
而它只在 `TOOL_GATEWAY=mcp` 时生效 —— 这种「只在某条路径上存在的规则」最难排查。

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
      "command": "E:/项目/ecom-cs-agent/scm_agent/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_server"],
      "cwd": "E:/项目/ecom-cs-agent"
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
