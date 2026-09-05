"""
Mini Agent 主程序入口
"""

import os
import sys
from dotenv import load_dotenv
from agent_runtime import AgentRuntime
from llm_client import LLMClient


def banner():
    print("""
╔══════════════════════════════════════════════════════╗
║     🤖  Mini Agent - 从零手写 ReAct Runtime        ║
║          原生 Python · 无框架依赖 · 完整闭环        ║
╚══════════════════════════════════════════════════════╝
    """)


def main():
    load_dotenv()

    # 检查 API Key
    if not os.getenv("DEEPSEEK_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("❌ 请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")
        sys.exit(1)

    try:
        # 初始化
        llm = LLMClient()
        agent = AgentRuntime(llm)

        banner()
        print(f"📦 模型: {llm.model}")
        print(f"🔧 工具: {', '.join(agent.tools.list())}")
        print("\n命令: /exit 退出 | /new 新会话 | /list 查看 | /switch <id> 切换")
        print("=" * 56 + "\n")

        sid = "default"

        while True:
            try:
                user_input = input("👤 你: ").strip()

                if not user_input:
                    continue

                # 命令处理
                if user_input.startswith('/'):
                    parts = user_input.split(maxsplit=1)
                    cmd = parts[0].lower()
                    arg = parts[1] if len(parts) > 1 else ""

                    if cmd == '/exit':
                        print("👋 再见！")
                        break
                    elif cmd == '/new':
                        sid = arg or input("新会话名称: ").strip()
                        agent.sessions.get(sid)
                        print(f"✅ 切换到: {sid}")
                        continue
                    elif cmd == '/list':
                        for s in agent.sessions.list():
                            print(f"  {s['id']} ({s['message_count']}条)")
                        continue
                    elif cmd == '/switch':
                        if agent.sessions.exists(arg):
                            sid = arg
                            print(f"✅ 切换到: {sid}")
                        else:
                            print(f"❌ 会话 '{arg}' 不存在")
                        continue
                    else:
                        print(f"未知命令: {cmd}")
                        continue

                # 正常对话
                print("\n🤔 Agent 思考中...")
                response = agent.run(user_input, sid)
                print(f"\n🤖 Agent: {response}\n")

            except KeyboardInterrupt:
                print("\n👋 再见！")
                break
            except Exception as e:
                print(f"❌ 错误: {e}")

    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()