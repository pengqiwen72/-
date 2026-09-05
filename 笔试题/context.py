"""
上下文管理：对话历史、轮次限制、超长压缩
"""

from typing import List, Dict, Any
from dataclasses import dataclass, field
import json


@dataclass
class Context:
    """对话上下文"""
    max_rounds: int = 15          # 最大轮次
    max_messages: int = 30        # 最大消息数
    messages: List[Dict] = field(default_factory=list)
    compressed: bool = False

    def add(self, role: str, content: Any) -> None:
        """添加消息"""
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        self.messages.append({"role": role, "content": str(content)})
        self._auto_compress()

    def get(self) -> List[Dict]:
        """获取所有消息"""
        return self.messages

    def rounds(self) -> int:
        """获取对话轮次"""
        return len([m for m in self.messages if m["role"] == "user"])

    def clear(self) -> None:
        """清空"""
        self.messages = []
        self.compressed = False

    def _auto_compress(self) -> None:
        """自动压缩：保留首尾"""
        if len(self.messages) <= self.max_messages:
            return

        # 分离系统消息
        system = [m for m in self.messages if m["role"] == "system"]
        others = [m for m in self.messages if m["role"] != "system"]

        if not others:
            return

        # 保留: 前2轮(4条) + 后5轮(10条)
        keep_front = min(4, len(others))
        keep_back = min(10, len(others) - keep_front)

        # 保留最近的工具调用
        tools = [m for m in others if m["role"] == "tool"][-3:]

        # 构建新消息
        new_msgs = system.copy()
        new_msgs.extend(others[:keep_front])

        # 压缩标记
        if len(others) > keep_front + keep_back:
            new_msgs.append({
                "role": "system",
                "content": f"[已压缩] 中间 {len(others) - keep_front - keep_back} 条消息已压缩"
            })
            self.compressed = True

        new_msgs.extend(others[-keep_back:])

        # 去重添加工具消息
        for t in tools:
            if t not in new_msgs:
                new_msgs.append(t)

        self.messages = new_msgs

    def info(self) -> Dict:
        """上下文信息"""
        return {
            "message_count": len(self.messages),
            "rounds": self.rounds(),
            "compressed": self.compressed
        }