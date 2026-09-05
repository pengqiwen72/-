"""
Agent 核心运行时：完整 ReAct 循环
这是本次作业的核心模块
"""

from typing import Dict, Optional
from utils import logger
from tools import tools
from llm_client import LLMClient
from session import SessionManager
from prompt import build_system_prompt


class AgentRuntime:
    """
    Agent 运行时 - 原生手写 ReAct 循环

    循环流程:
    1. 接收用户输入 → 放入上下文
    2. 调用 LLM 判断：直接回答 / 调用工具
    3. 解析工具调用 → 执行工具
    4. 根据工具结果：继续循环 / 输出最终答案
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.sessions = SessionManager()
        self.tools = tools
        self.max_iterations = 8  # 防死循环

        # 构建系统提示词
        self.system_prompt = build_system_prompt(
            self.tools.get_description()
        )

        logger.info("Agent 初始化", {
            "tools": self.tools.list(),
            "max_iter": self.max_iterations
        })

    def run(self, user_input: str, session_id: str = "default") -> str:
        """
        Agent 主循环入口
        """
        # 获取会话上下文
        session = self.sessions.get(session_id)
        ctx = session.context

        # 添加用户消息
        ctx.add("user", user_input)
        logger.info(f"用户输入", {"session": session_id, "input": user_input[:50]})

        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"迭代 {iteration}", {"session": session_id})

            try:
                # 构建消息
                messages = self._build_messages(ctx)

                # 调用 LLM
                response = self.llm.chat(messages, self.tools.get_schemas())

                # 检查是否 API 调用失败
                if response.startswith("API 调用失败"):
                    ctx.add("assistant", response)
                    return response

                # 解析响应
                action = self._parse_action(response)

                if action is None:
                    error = "无法解析 LLM 响应，请重试"
                    logger.error(error, {"response": response[:200]})
                    ctx.add("assistant", error)
                    return error

                if action["type"] == "tool_call":
                    # === 执行工具 ===
                    tool_name = action["tool_name"]
                    args = action["arguments"]

                    # 验证工具是否存在
                    if not self.tools.get(tool_name):
                        error = f"未知工具: {tool_name}"
                        logger.error(error)
                        ctx.add("assistant", error)
                        return error

                    logger.tool(f"调用工具", {"tool": tool_name, "args": args})

                    result = self._execute_tool(tool_name, args, session_id)

                    # 记录工具结果
                    ctx.add("tool", {"tool": tool_name, "result": result})

                    logger.tool("工具完成", {
                        "tool": tool_name,
                        "result": result[:100] if len(result) > 100 else result
                    })

                    # 继续循环
                    continue

                elif action["type"] == "answer":
                    # === 输出最终答案 ===
                    ctx.add("assistant", action["content"])
                    logger.info("Agent 回答", {"session": session_id, "answer": action["content"][:50]})
                    return action["content"]

                else:
                    error = f"未知响应类型: {action}"
                    logger.error(error)
                    ctx.add("assistant", error)
                    return error

            except Exception as e:
                error = f"Agent 执行异常: {str(e)}"
                logger.error(error, {"session": session_id})
                ctx.add("assistant", error)
                return error

        # 达到最大迭代
        error = f"达到最大迭代次数 {self.max_iterations}，请简化请求"
        logger.warn(error, {"session": session_id})
        ctx.add("assistant", error)
        return error

    def _build_messages(self, ctx) -> list:
        """构建消息列表"""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(ctx.get())
        return messages

    def _parse_action(self, response: str) -> Optional[Dict]:
        """
        解析 LLM 响应

        返回:
            {"type": "tool_call", "tool_name": str, "arguments": dict}
            或 {"type": "answer", "content": str}
            或 None（解析失败）
        """
        if not response:
            return None

        # 尝试提取工具调用
        tool_call = self.llm.parse_tool_call(response)

        if tool_call and "tool" in tool_call:
            return {
                "type": "tool_call",
                "tool_name": tool_call["tool"],
                "arguments": tool_call.get("arguments", {})
            }

        # 检查是否包含 ```json 代码块
        if "```json" in response and '"tool"' in response:
            try:
                import json
                start = response.find("```json") + 7
                end = response.find("```", start)
                if start >= 0 and end > start:
                    data = json.loads(response[start:end].strip())
                    if "tool" in data:
                        return {
                            "type": "tool_call",
                            "tool_name": data["tool"],
                            "arguments": data.get("arguments", {})
                        }
            except:
                pass

        # 普通回答
        return {"type": "answer", "content": response}

    def _execute_tool(self, tool_name: str, args: Dict, session_id: str) -> str:
        """执行工具"""
        tool = self.tools.get(tool_name)
        if not tool:
            return f"错误: 未知工具 '{tool_name}'"

        try:
            return tool.execute(**args)
        except Exception as e:
            return f"工具执行失败: {str(e)}"