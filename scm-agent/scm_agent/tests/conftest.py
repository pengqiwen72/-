"""
pytest 公共装置：把测试钉在「离线 + 进程内」这一条轨道上。

这个文件守三件事
----------------
1. **测试永远不联网。** `LLM_MODE=offline` / `INTEGRATION_MODE=mock` /
   `TOOL_GATEWAY=direct` 必须在**任何 src 代码被 import 之前**写进环境变量：
   `llm/llm_setting.py`、`integrations/factory.py` 都在 import 期或首次调用时
   就读环境，晚一步就会真的去连 dashscope / 企业微信。这里用直接赋值而不是
   `setdefault`——`setdefault` 在有人导出过 `LLM_MODE=live` 的机器上会放行，
   测试就变成「本地绿、CI 红」甚至「偷偷烧 token」。
   （`python-dotenv` 的 `load_dotenv()` 默认不覆盖已有变量，所以 `scm_agent/.env`
   里那些真实 key 不会顶掉这里的设置。）

2. **测试不与源码目录耦合。** 只把 `scm_agent/src` 挂到 `sys.path`，
   与 `quickstart.py` 同一套做法；测试文件自身放在 `scm_agent/tests/` 下。

3. **不收集前端回归脚本。** `tests/frontend/timeline_check.mjs` 是给 node 跑的
   （见 memory 约定第 11 条），pytest 碰它只会得到 collection error。
   `collect_ignore` 是显式声明：即使将来有人在 pyproject 里放开 `python_files`，
   这里也有一道闸。

用例隔离
--------
`_isolated_runtime` 每个用例前后都重置端口单例与兜底 store：
端口是 `lru_cache` 的进程级单例，store 里存着幂等/审批记录，不重置的话
「上一个用例执行过退款」会让下一个用例的幂等断言变成假绿。

⚠️ 光重置兜底 store 还不够：`tools/store_tool.py` 里的 `store_client` 是模块级
单例，而 `StoreClient.store` 是**首次访问时解析并缓存**的 —— 它攥着的是
「会话里第一个被解析到的 store」。用例 A 写进这个 store 的标签，用例 B 读出来
就会有（顺序相关的假红/假绿）。所以这里连缓存引用一起清掉，让每个用例
重新解析到自己那一个 store。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- 1. 环境钉死
# ⚠️ 必须在 import 任何 src 模块之前执行 —— 本文件是 pytest 最先加载的模块之一。
os.environ["LLM_MODE"] = "offline"
os.environ["INTEGRATION_MODE"] = "mock"
os.environ["TOOL_GATEWAY"] = "direct"

# ---------------------------------------------------------------- 2. sys.path
_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# ---------------------------------------------------------------- 3. 收集边界
# 前端时间线回归是 node 脚本，不属于 pytest 的收集范围
collect_ignore = ["frontend"]
collect_ignore_glob = ["frontend/*"]

import pytest  # noqa: E402


def pytest_configure(config):
    """静音 langgraph 1.0 的 `config_schema` 弃用警告。

    它来自 `scm_graph.py` 等源码里的构造调用（V2 才会移除），每个用例刷一屏，
    会把真正的失败信息淹掉。只滤这一条，别的警告照常报出来。
    """
    config.addinivalue_line(
        "filterwarnings",
        r"ignore:.*config_schema.*:DeprecationWarning",
    )


@pytest.fixture(autouse=True)
def _isolated_runtime():
    """每个用例一套干净的端口单例与内存 store。"""
    from integrations.factory import reset_ports
    from store.store_provider import reset_fallback_store

    import tools.store_tool as store_tool

    def _reset() -> None:
        from kg.store import reset_graph_store
        from observability import degrade as degrade_mod

        reset_ports()
        reset_fallback_store()
        # 模块级 store_client 缓存的 store 引用也要清（见文件头「用例隔离」）
        store_tool.store_client._store = None
        # 图存储是从种子投影的进程级单例；降级级别是进程级 module globals。
        # 不重置的话，上一个用例的图故障注入（MOCK_FAIL_ACTIONS=graph）
        # 会把 L1 状态留给下一个用例，问答断言变成顺序相关的假红/假绿。
        reset_graph_store()
        degrade_mod.reset()

    _reset()
    try:
        yield
    finally:
        # 用例里 inject 过 MOCK_FAIL_ACTIONS 之类的环境变量时，
        # monkeypatch 只负责还原 os.environ；端口缓存仍需再清一次，
        # 否则它会把「带着故障注入环境」的实例留给下一个用例
        _reset()


@pytest.fixture
def knowledge_base():
    """离线商品知识库（按语料指纹缓存，整个会话只构建一次）。"""
    from rag.index import get_knowledge_base

    return get_knowledge_base()


@pytest.fixture
def demo_graph():
    """一个进程内的演示图（InMemorySaver + 已灌种子的离线 store）。"""
    from demo import build_demo_graph

    return build_demo_graph()


@pytest.fixture
def runner(demo_graph):
    """驱动演示图的运行器；`thread_config` 会带上 user_id / external_id。"""
    from demo import DemoRunner

    return DemoRunner(demo_graph)
