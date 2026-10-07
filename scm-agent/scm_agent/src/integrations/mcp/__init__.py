"""
MCP 工具网关的客户端侧（依赖 MCP SDK，仅在 `TOOL_GATEWAY=mcp` 时被导入）。

⚠️ 本包**刻意不在 `__init__` 里 import 任何子模块**：SDK 是可选依赖，
而 `integrations.factory` 会在 import 期就判断该走哪条路径 —— 如果这里
提前把 `bridge` 拉起来，没装 SDK 的环境连 `TOOL_GATEWAY=direct` 都跑不了。
`envelope.py` 之所以能单独 import，也是因为它不碰 SDK。
"""
