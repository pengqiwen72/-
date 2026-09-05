"""
测试用例：单轮对话、多轮追问、工具循环、多 session 隔离
"""

import os
import sys
import time
from dotenv import load_dotenv
from agent_runtime import AgentRuntime
from llm_client import LLMClient


def test_basic():
    """基础测试：单轮对话"""
    print("\n" + "=" * 50)
    print("测试 1: 单轮对话")
    print("=" * 50)

    agent = AgentRuntime(LLMClient())

    # 测试计算器
    r = agent.run("计算 25 * 4 + 10", "test1")
    print(f"计算器: {r}")
    assert "110" in r, "计算器测试失败"

    # 测试天气
    r = agent.run("查询杭州天气", "test1")
    print(f"天气: {r}")
    assert "天气" in r or "杭州" in r, "天气测试失败"

    # 测试搜索
    r = agent.run("搜索 Python", "test1")
    print(f"搜索: {r}")
    assert "Python" in r, "搜索测试失败"

    print("✅ 基础测试通过")


def test_multi_turn():
    """多轮追问测试"""
    print("\n" + "=" * 50)
    print("测试 2: 多轮对话追问")
    print("=" * 50)

    agent = AgentRuntime(LLMClient())
    sid = "test_multi"

    # 第一轮
    r1 = agent.run("我叫张三", sid)
    print(f"轮1: {r1}")

    # 第二轮（追问）
    r2 = agent.run("我叫什么名字？", sid)
    print(f"轮2: {r2}")
    assert "张三" in r2, "多轮追问失败"

    print("✅ 多轮对话测试通过")


def test_session_isolation():
    """多 Session 隔离测试"""
    print("\n" + "=" * 50)
    print("测试 3: 多 Session 隔离")
    print("=" * 50)

    agent = AgentRuntime(LLMClient())

    # Session A
    agent.run("我叫张三", "session_A")
    agent.run("我喜欢吃苹果", "session_A")

    # Session B
    agent.run("我叫李四", "session_B")
    agent.run("我喜欢吃香蕉", "session_B")

    # 检查 Session A
    r_a = agent.run("我叫什么名字？", "session_A")
    print(f"Session A: {r_a}")
    assert "张三" in r_a, "Session A 应该记得叫张三"

    # 检查 Session B
    r_b = agent.run("我叫什么名字？", "session_B")
    print(f"Session B: {r_b}")
    assert "李四" in r_b, "Session B 应该记得叫李四"

    print("✅ Session 隔离测试通过")


def run_all_tests():
    """运行所有测试"""
    load_dotenv()

    if not os.getenv("DEEPSEEK_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("❌ 请设置 API Key")
        return

    print("\n🧪 开始运行测试套件...")

    tests = [
        test_basic,
        test_multi_turn,
        test_session_isolation,
    ]

    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"❌ 测试失败: {e}")
        except Exception as e:
            print(f"❌ 测试异常: {e}")

        time.sleep(0.5)

    print("\n" + "=" * 50)
    print("✅ 所有测试完成！")


if __name__ == "__main__":
    run_all_tests()