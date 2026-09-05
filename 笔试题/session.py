"""
多 Session 管理：隔离对话上下文
"""

from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from context import Context


@dataclass
class Session:
    """会话"""
    id: str
    context: Context = field(default_factory=Context)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "message_count": len(self.context.messages),
            "rounds": self.context.rounds()
        }


class SessionManager:
    """会话管理器"""

    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def get(self, sid: str) -> Session:
        """获取或创建会话"""
        if sid not in self._sessions:
            self._sessions[sid] = Session(id=sid)
        return self._sessions[sid]

    def delete(self, sid: str) -> bool:
        if sid in self._sessions:
            del self._sessions[sid]
            return True
        return False

    def list(self) -> List[Dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def exists(self, sid: str) -> bool:
        return sid in self._sessions