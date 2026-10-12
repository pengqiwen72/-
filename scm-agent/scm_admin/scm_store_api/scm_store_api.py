"""
审批看板的 Store 客户端：从 LangGraph 平台的 Store 里读审批记录。

数据**不在本地库里**：审批记录由业务链路写在平台 Store 的
`("approval",)` 命名空间（见 `scm_agent/src/integrations/idempotency.py`
的 `record_approval`）。所以看板必须先起平台服务：

    scm_agent/run_scm_agent        # langgraph dev，:2024

这个文件里没有任何写接口 —— 看板是给人看结果的，不是给人改数据的。
审批本身发生在演示工作台（interrupt → 人工决策的那一步），看板是事后审计
的视角：谁批的、第几轮批的、有没有返工。

关于令牌：平台服务的每个接口都要 Bearer，Store 也不例外，所以这里仍然要
走一遍 `/scm/get_token`。用的是演示账号（`SCM_USER_ID`），不是另一套
「管理员账号」—— 看板不需要额外权限模型，它读的本来就是演示数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langgraph_sdk import get_sync_client
import requests

load_dotenv()

#: 与 `scm_agent/src/integrations/idempotency.py::APPROVAL_NAMESPACE` 保持同一口径。
#: 平台 Store 的命名空间是字符串列表，这里写单元素列表。
APPROVAL_NAMESPACE = ["approval"]


class ScmStoreAPI:
    """平台 Store 的只读客户端（当前只有审批记录一个入口）。"""

    def __init__(self, server_url: str, user_id: str, password: str):
        if not server_url or not user_id or not password:
            raise ValueError("server_url, user_id, password are required")

        response = requests.post(
            f"{server_url}/scm/get_token",
            json={"user_id": user_id, "password": password},
            timeout=10,
        )
        if response.status_code != 200:
            raise ValueError(
                f"获取 token 失败（HTTP {response.status_code}）："
                "确认平台服务已启动、SCM_USER_ID / SCM_PASSWORD 与 .env 一致"
            )
        token = response.json().get("token")

        self.store = get_sync_client(
            url=server_url, headers={"Authorization": "Bearer " + token}
        ).store

    # ==================== 审批记录 ====================

    def list_approvals(self, limit: int = 500) -> List[Dict[str, Any]]:
        """读出 `("approval",)` 下的全部审批记录。

        每条记录一行、**每轮审批各一行**（键是 `线程:方案[:r轮次]`，见
        `approval_key`）—— 看板要算「一次通过率」，就必须看得到轮次，
        所以这里不做去重，聚合交给上层。

        没有配 `filter`：这个命名空间下只有审批记录，全量读回即可。
        """
        response = self.store.search_items(APPROVAL_NAMESPACE, limit=limit)
        items = response["items"] if isinstance(response, dict) else response
        records: List[Dict[str, Any]] = []
        for item in items:
            item = item or {}
            value = dict(item.get("value") or {})
            value["key"] = item.get("key", "")
            records.append(value)
        return records


def create_scm_store_api(url: str, user_id: str, password: str) -> ScmStoreAPI:
    return ScmStoreAPI(server_url=url, user_id=user_id, password=password)
