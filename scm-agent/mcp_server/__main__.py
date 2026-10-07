"""
入口：`python -m mcp_server`。

Agent 侧的 `bridge.server_command()` 默认就是这条命令，用 `sys.executable`
拼出来 —— 保证网关跑在**同一个解释器**里，不会出现「Agent 装在 venv 里、
网关却跑到系统 Python 上、于是读不到同一个配置文件」这种排查起来很费劲的问题。
"""

from mcp_server.server import main

if __name__ == "__main__":
    main()
