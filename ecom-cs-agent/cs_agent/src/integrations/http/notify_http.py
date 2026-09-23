"""
通知 / 日程的真实实现：包一层企业微信（WxWorkAPI）。

只有 INTEGRATION_MODE=http 时才会走到这里。注意 `WxWorkAPI` 在缺少环境变量时
构造即抛 ValueError，所以这里**延迟构造**——离线场景根本不会实例化它，
不会因为缺配置导致整个包导入失败。
"""

from __future__ import annotations

from integrations.ports import NotifyPort, SchedulePort
from utils.agent_logger import get_logger

logger = get_logger("notify_http")


class WxWorkNotifyPort(NotifyPort):
    def __init__(self):
        self._api = None

    @property
    def api(self):
        if self._api is None:
            from tools.wechat_tool import WxWorkAPI

            self._api = WxWorkAPI()
        return self._api

    def notify(self, user_id: str, content: str, title: str = "") -> bool:
        try:
            return bool(self.api.notify_user(user_id=user_id, content=content, title=title))
        except Exception as e:
            logger.error(f"企业微信通知失败：{e}")
            return False


class WxWorkSchedulePort(SchedulePort):
    def __init__(self):
        self._api = None

    @property
    def api(self):
        if self._api is None:
            from tools.wechat_tool import WxWorkAPI

            self._api = WxWorkAPI()
        return self._api

    def create_schedule(
        self, user_id: str, title: str, start_time: str, duration_minutes: int = 30
    ) -> bool:
        try:
            return bool(
                self.api.create_schedule(
                    user_id=user_id,
                    title=title,
                    start_time=start_time,
                    duration_minutes=duration_minutes,
                )
            )
        except Exception as e:
            logger.error(f"企业微信创建日程失败：{e}")
            return False
