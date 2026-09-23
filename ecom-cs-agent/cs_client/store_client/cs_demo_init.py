"""
把演示种子数据灌进**平台**的 Store（服务模式用）。

与离线模式的关系
----------------
离线跑 demo（`python quickstart.py`）时，种子数据由 `cs_agent` 的
`store/seed_loader.py` 灌进进程内的 InMemoryStore，不依赖任何服务。

服务模式下数据在 LangGraph 平台的 Store 里，进程内那份灌不进去 ——
工作台和运营后台打开就是一片空白。这个脚本干的是同一件事，只是写入目标
换成平台：**读同一份 `cs_agent/data/seed/*.json`**，连「相对时间怎么回推成
绝对时间」都复用同一份实现。

所以这里没有第二套演示数据 —— 数据只有那一份，两个出口。改动哪边都不会
出现「离线看着对、平台里是旧的」这种错位。

用法
----
    # 先起平台服务
    cs_agent/run_cs_agent
    # 再灌数据（幂等，可以重复跑）
    python cs_demo_init.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 复用 cs_agent 的种子加载器：把它的 src 挂到 sys.path。
# 与 mcp_server/__init__.py 同一套做法 —— 两个独立工程之间共享代码，
# 与其抄一份不如让路径显式地跨过去。
_AGENT_SRC = Path(__file__).resolve().parents[2] / "cs_agent" / "src"
if _AGENT_SRC.is_dir() and str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

from cs_store_api import create_cs_store_api  # noqa: E402


class _PlatformStore:
    """把 SDK 的 store 包成 `seed_loader` 认的接口。

    seed_loader 是照 LangGraph 的 `BaseStore` 写的（`store.put(namespace, key, value)`），
    而平台 SDK 的客户端叫 `put_item`，namespace 要 list 不要 tuple。
    这里只对齐这两处，其余逻辑一概不动 —— 种子数据怎么算时间、怎么写，
    仍然只有那一份实现。
    """

    def __init__(self, store):
        self._store = store

    def put(self, namespace, key, value):
        self._store.put_item(list(namespace), key, value)


def main() -> int:
    server_url = os.getenv("CS_SERVER_URL")
    user_id = os.getenv("CS_USER_ID")
    password = os.getenv("CS_USER_PASSWORD")

    missing = [
        name for name, value in (
            ("CS_SERVER_URL", server_url),
            ("CS_USER_ID", user_id),
            ("CS_USER_PASSWORD", password),
        ) if not value
    ]
    if missing:
        print(
            f"缺少配置 {', '.join(missing)}：请复制 .env.example 为 .env 并填写，"
            "并确认平台服务（cs_agent/run_cs_agent）已启动"
        )
        return 2

    try:
        client = create_cs_store_api(server_url, user_id, password)
    except Exception as e:
        print(f"连接平台失败：{e}")
        return 1

    from store.seed_loader import load_seed_data

    stats = load_seed_data(_PlatformStore(client.store))

    print("平台 Store 种子数据写入完成（重复执行只覆盖同样的键）：")
    for name, count in stats.items():
        print(f"  {name:<18} {count} 条")
    print(f"  {'合计':<16} {sum(stats.values())} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
