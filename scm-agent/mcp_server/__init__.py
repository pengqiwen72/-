"""
MCP 业务工具网关（stdio）。

它是一个**独立进程**：Agent 通过 MCP 协议（JSON-RPC over stdio）向它请求
订单、物流、库存、售后等业务能力，而不是在进程内直接调函数。

为什么值得多这一层
------------------
「工具」和「Agent」是两类变化节奏完全不同的东西。工具面对的是业务系统的
接口变更、权限、灰度；Agent 面对的是提示词、编排、模型版本。把工具拆成一个
说标准协议的独立进程之后：

  * 工具实现可以单独部署、单独发版、单独限流，不必跟着 Agent 一起重启；
  * 同一套工具能被任何 MCP 客户端复用（Claude Desktop、IDE 插件、别的 Agent），
    不必为每个接入方各写一份 SDK；
  * 换成第三方工具集时，改的是 `MCP_SERVER_COMMAND` 指向谁，Agent 侧零改动。

这里是**服务端**。客户端的实现在 `scm_agent/src/integrations/mcp/`。

关于下面这段 sys.path 引导
--------------------------
网关要复用 `scm_agent` 里已经写好的端口实现（mock 读种子、http 调真实系统），
所以把 `scm_agent/src` 挂进自己的 sys.path。这是**演示形态下的省事做法**：
真正做到独立部署时，这一段的替代品是「网关自己持有数据库连接 / HTTP 客户端」，
而它对外暴露的工具契约一行都不用变 —— 这也正是把这层拆出来的意义。
"""

import os
import sys
from pathlib import Path

_AGENT_SRC = Path(__file__).resolve().parents[1] / "scm_agent" / "src"
if _AGENT_SRC.is_dir() and str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

# ⚠️ 网关自己**绝不**再走一次网关。客户端已经这么设了，但这里再硬保一道：
# 手工用 `TOOL_GATEWAY=mcp python -m mcp_server` 启动时，少了这一行就是
# 「网关调网关调网关」，栈溢出比报错更难看出原因。
os.environ["TOOL_GATEWAY"] = "direct"
