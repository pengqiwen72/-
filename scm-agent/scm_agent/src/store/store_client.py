from store.store_provider import resolve_store
from utils.debug_aspect import debug
from typing import List, Dict, Any, Optional
from langgraph.store.base import SearchItem, Item, BaseStore

# 「要全部」时用的条数上限。search() 的默认 limit 是 10，而标签体系、员工名册
# 这类全量查询显然不止 10 条 —— 不显式传就会静默丢数据。
_SEARCH_ALL = 10000


class StoreClient:
    """
    长期记忆（LangGraph Store）访问封装。

    注意：store 必须**懒加载**——脱离 LangGraph 运行时时 `get_store()` 会抛
    RuntimeError，若在构造时解析会导致模块导入即失败（离线 demo 无法运行）。
    """

    def __init__(self, store: Optional[BaseStore] = None):
        # 允许显式注入（测试用）；默认延迟到首次访问时解析
        self._store = store

    @property
    def store(self) -> BaseStore:
        """首次访问时解析可用的 store（运行时 store 优先，离线回落 InMemoryStore）。"""
        if self._store is None:
            self._store = resolve_store()
        return self._store

    # ==================== 辅助工具 ====================
    def item2dict(self, item: Item) -> Dict[str, Any]:
        if not item:
            return {}
        if isinstance(item.value, dict):
            return item.value.copy()  ## 避免影响原值
        return item.value

    def search_items_to_dict_list(self, searchitems: List[SearchItem]) -> List[Dict[str, Any]]:
        if not searchitems:
            return []
        return [self.item2dict(item) for item in searchitems]


    # ==================== 通用方法 ====================
    @debug
    def get_item(self, namespace: tuple, key: str) -> Dict[str, Any]:
        item = self.store.get(namespace, key)
        return self.item2dict(item)

    @debug
    def get_all_namespaces(self, limit: int = 100) -> list:
        return self.store.list_namespaces(limit=limit)

    # ==================== 员工信息 ====================

    @debug
    def get_employee_by_user_id(self, user_id: str) -> Dict[str, Any]:
        if not user_id:
            return {}
        item = self.store.get(("employee",), user_id)
        return self.item2dict(item)

    @debug
    def list_all_employee(self) -> List[Dict[str, Any]]:
        namespace = ("employee",)
        # ⚠️ 必须显式传 limit：search() 默认只返回 10 条，
        #    不传的话数据一多就被静默截断（不报错，只是少了几条）
        search_result = self.store.search(namespace, limit=_SEARCH_ALL)
        return self.search_items_to_dict_list(search_result)

    # ==================== 标签信息 ====================

    @debug
    def get_tags_setting_by_tag_id(self, tag_id: str) -> Dict[str, Any]:
        if not tag_id:
            return {}
        item = self.store.get(("tags_setting",), tag_id)
        return self.item2dict(item)

    @debug
    def list_all_tags_setting(self) -> List[Dict[str, Any]]:
        namespace = ("tags_setting",)
        filter = {"deleted": False}
        # ⚠️ 同样的问题：默认 limit=10 会把标签体系砍掉一大半，
        #    后果是标签建议永远只在那 10 个里挑，且完全看不出哪里错了。
        search_result = self.store.search(namespace, filter=filter, limit=_SEARCH_ALL)
        return self.search_items_to_dict_list(search_result)


    # ==================== 外部客户信息 ====================
    @debug
    def get_external_user_by_external_id(self, external_id: str, follow_user_id: str) -> Dict[str, Any]:
        """根据external_id获取外部客户信息"""

        if not external_id or not follow_user_id:
            return {}

        namespace = ("external_user", follow_user_id)
        key = external_id
        item = self.store.get(namespace, key)
        return self.item2dict(item)

    @debug
    def get_external_user_tag_by_external_id(self, external_id: str, follow_user_id: str) -> List[Dict[str, Any]]:
        """根据external_id获取客户标签"""

        external_user = self.get_external_user_by_external_id(external_id, follow_user_id)

        tag_ids = external_user.get("tags", [])

        tag_details = []
        
        for tag_id in tag_ids:
            tag = self.get_tags_setting_by_tag_id(tag_id)
            if tag: 
                tag_details.append(tag)

        return tag_details

    @debug
    def upsert_external_user_tag_by_external_id(self, external_id: str, follow_user_id: str, tag_ids: List[str]) -> bool:
        """根据external_id获取客户标签"""

        if not external_id or not follow_user_id:
            return False

        if not tag_ids:
            tag_ids = []

        external_user = self.get_external_user_by_external_id(external_id, follow_user_id)

        if not external_user:
            return False
        
        external_user["tags"] = tag_ids

        namespace = ("external_user", follow_user_id)
        key = external_id
        self.store.put(namespace, key, external_user)


        return True

    @debug
    def get_profile_by_external_id(self, external_id: str, follow_user_id: str) -> Dict[str, Any]:
        """根据external_id获取客户画像"""

        if not external_id or not follow_user_id:
            return {}

        namespace = ("external_user_profile", follow_user_id)
        key = external_id
        item = self.store.get(namespace, key)
        return self.item2dict(item)

    @debug
    def upsert_external_user_profile(self, external_id: str, follow_user_id: str, profile: Dict[str, Any]) -> bool:
        """更新外部客户信息"""

        if not external_id or not follow_user_id:
            return False

        namespace = ("external_user_profile", follow_user_id)
        key = external_id
        self.store.put(namespace, key, profile)
        
        return True

    # ==================== 聊天消息信息 ====================
    @debug
    def list_last_wxqy_msg(self, external_id: str, follow_user_id: str, after_yyyy_mm_dd: Optional[str] = None, limit: Optional[int] = 100) -> List[Dict[str, Any]]:
        """获取近期聊天消息内容信息，支持过滤条件"""

        if not external_id or not follow_user_id:
            return []

        from_to_sorted_key = "".join(sorted([external_id, follow_user_id]))
        namespace = ("wxqy_msg", from_to_sorted_key)
        if after_yyyy_mm_dd:
            filter = {"YYYYMMDD": {"$gte": after_yyyy_mm_dd}}
        else:
            filter = {}
        search_result = self.store.search(namespace, filter=filter, limit=limit)
        return self.search_items_to_dict_list(search_result)


    # ==================== 微信客服信息 ====================
    @debug
    def list_last_wxkf_msg(self, external_id: str, after_yyyy_mm_dd: Optional[str] = None, limit: Optional[int] = 100) -> List[Dict[str, Any]]:
        """获取近期微信客服信息，支持过滤条件"""

        if not external_id:
            return []

        namespace = ("wxkf_msg", external_id)
        if after_yyyy_mm_dd:
            filter = {"YYYYMMDD": {"$gte": after_yyyy_mm_dd}}
        else:
            filter = {}
        search_result = self.store.search(namespace, filter=filter, limit=limit)
        return self.search_items_to_dict_list(search_result)


    # ==================== 客户订单信息 ====================
    @debug
    def list_wxxd_order_by_union_id(self, union_id: str, after_yyyy_mm_dd: Optional[str] = None, limit: Optional[int] = 100) -> List[Dict[str, Any]]:
        """根据union_id获取客户订单信息列表"""

        if not union_id:
            return []

        namespace = ("wxxd_order", union_id)

        if after_yyyy_mm_dd:
            filter = {"YYYYMMDD": {"$gte": after_yyyy_mm_dd}}
        else:
            filter = {}

        search_result = self.store.search(namespace, filter=filter, limit=limit)
        return self.search_items_to_dict_list(search_result)