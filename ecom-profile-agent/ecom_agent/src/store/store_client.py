from langgraph.config import get_store
from utils.debug_aspect import debug
from typing import List, Dict, Any, Optional
from langgraph.store.base import SearchItem, Item

class StoreClient:
    def __init__(self):
        self.store = get_store()

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
        search_result = self.store.search(namespace)
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
        search_result = self.store.search(namespace, filter=filter)
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
        """根据external_id获取客户profile"""

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