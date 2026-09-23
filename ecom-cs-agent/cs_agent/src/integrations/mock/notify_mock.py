"""
通知 / 日程的 mock 实现。

不发起任何网络请求：把消息落到 LangGraph Store 的 `notification` 命名空间，
终端打印一行。这样 demo 里能看到「已通知坐席 CS001：……」，后台也能查到记录，
但完全离线。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from langgraph.store.base import BaseStore

from integrations.ports import NotifyPort, SchedulePort
from store.store_provider import get_fallback_store
from utils.agent_logger import get_logger

logger = get_logger("notify_mock")

NOTIFICATION_NAMESPACE = ("notification",)
SCHEDULE_NAMESPACE = ("schedule",)


class MockNotifyPort(NotifyPort):
    """把通知写入 store + 日志。"""

    def __init__(self, store: Optional[BaseStore] = None):
        self._store = store

    @property
    def store(self) -> BaseStore:
        if self._store is None:
            self._store = get_fallback_store()
        return self._store

    def notify(self, user_id: str, content: str, title: str = "") -> bool:
        if not user_id:
            logger.warning("通知被跳过：缺少 user_id")
            return False

        record = {
            "notification_id": f"NT-{uuid.uuid4().hex[:10]}",
            "user_id": user_id,
            "title": title or "智能客服助手",
            "content": content,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": "mock",
        }

        try:
            self.store.put(NOTIFICATION_NAMESPACE, record["notification_id"], record)
        except Exception as e:  # pragma: no cover - 兜底不阻断业务
            logger.warning(f"通知落库失败（不影响主流程）：{e}")

        preview = content.replace("\n", " ")[:60]
        logger.info(f"[MOCK 通知] -> {user_id}：{preview}{'...' if len(content) > 60 else ''}")
        return True


class MockSchedulePort(SchedulePort):
    """把日程写入 store + 日志。"""

    def __init__(self, store: Optional[BaseStore] = None):
        self._store = store

    @property
    def store(self) -> BaseStore:
        if self._store is None:
            self._store = get_fallback_store()
        return self._store

    def create_schedule(
        self, user_id: str, title: str, start_time: str, duration_minutes: int = 30
    ) -> bool:
        if not user_id or not title or not start_time:
            logger.warning("创建日程被跳过：参数不完整")
            return False

        schedule_id = f"SC-{uuid.uuid4().hex[:10]}"
        record = {
            "schedule_id": schedule_id,
            "user_id": user_id,
            "title": title,
            "start_time": start_time,
            "duration_minutes": duration_minutes,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": "mock",
        }

        try:
            self.store.put(SCHEDULE_NAMESPACE, schedule_id, record)
        except Exception as e:  # pragma: no cover
            logger.warning(f"日程落库失败（不影响主流程）：{e}")

        logger.info(f"[MOCK 日程] -> {user_id}：{title} @ {start_time}（{duration_minutes} 分钟）")
        return True
