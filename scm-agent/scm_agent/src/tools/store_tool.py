from typing import List, Dict, Any
from models.scm_models import CustomerInfo, TagInfo, ChatHistory, KFChatHistory, EmployeeInfo
from models.scm_models import OrderInfo, OrderHistory, TagSetting, ChatMessage, CustomerProfile, CustomerTags
from store.store_client import StoreClient
from utils.agent_logger import get_logger
from utils.debug_aspect import debug
from utils.datetime_string import timestamp2datetime

store_client = StoreClient()

logger = get_logger("store_tool")

@debug
def get_employee_info(user_id: str) -> EmployeeInfo:
    """获取员工信息"""

    employee_info = EmployeeInfo()
    employee = store_client.get_employee_by_user_id(user_id)

    if not employee:
        logger.error(f"获取员工信息失败: {user_id}")
        return employee_info

    employee_info.user_id = employee.get("user_id", "")
    employee_info.name = employee.get("name", "")

    return employee_info


@debug
def get_customer_info(external_id: str, follow_user_id: str) -> CustomerInfo:
    """获取客户信息"""
    
    customer_info = CustomerInfo()
    
    if not external_id or not follow_user_id:
        logger.error(f"获取客户时，参数缺失: {external_id}, {follow_user_id}")
        return customer_info
    
    # 获取客户信息
    external_user = store_client.get_external_user_by_external_id(external_id, follow_user_id)

    if not external_user:
        logger.error(f"获取客户信息失败: {external_id} {follow_user_id}")
        return customer_info

    customer_info.external_id = external_user.get("external_id")
    customer_info.union_id = external_user.get("union_id")
    customer_info.follow_user_id = external_user.get("follow_user_id")
    
    # 获取客户昵称
    customer_info.nick_name = external_user.get("remark_name", external_user.get("name", ""))

    return customer_info


@debug
def get_customer_tags(external_id: str, follow_user_id: str) -> CustomerTags:
    
    # 获取客户标签
    external_user_tags = store_client.get_external_user_tag_by_external_id(external_id, follow_user_id)

    customer_tags = CustomerTags()
    for tag in external_user_tags:
        tag_info = TagInfo(tag_id=tag.get("tag_id"), tag_name=tag.get("tag_name"))
        customer_tags.customer_tags.append(tag_info)
    return customer_tags

@debug
def update_customer_tags(external_id: str, follow_user_id: str, tag_ids_add: List[str], tag_ids_remove: List[str]) -> bool:
    """更新客户标签"""
    if not external_id or not follow_user_id:
        return False
    
    if not tag_ids_add:
        tag_ids_add = []

    if not tag_ids_remove:
        tag_ids_remove = []
        
    external_user = store_client.get_external_user_by_external_id(external_id, follow_user_id)
    
    if not external_user:
        return False
    
    tag_ids: List[str] = external_user.get("tags", [])
    
    if tag_ids_add:
        tag_ids.extend([tag_id for tag_id in tag_ids_add if tag_id not in tag_ids])
    
    if tag_ids_remove:
        tag_ids = [tag_id for tag_id in tag_ids if tag_id not in tag_ids_remove]
    
    
    return store_client.upsert_external_user_tag_by_external_id(external_id, follow_user_id, tag_ids)


@debug
def get_customer_profile(external_id: str, follow_user_id: str) -> CustomerProfile:
    """获取客户画像"""
    
    profile_dict = store_client.get_profile_by_external_id(external_id, follow_user_id)

    if not profile_dict:
        return CustomerProfile()
    
    try:
        return CustomerProfile.model_validate(profile_dict)
    except Exception as e:
        logger.error(f"获取客户画像失败: {e}")
        return CustomerProfile()
    
@debug
def update_customer_profile(external_id: str, follow_user_id: str, profile: CustomerProfile) -> bool:
    """更新客户画像"""
    if not external_id or not follow_user_id or not profile:
        return False
        
    return store_client.upsert_external_user_profile(external_id, follow_user_id, profile.model_dump())  


@debug
def get_chat_history(external_id: str, follow_user_id: str) -> ChatHistory:
    """获取员工/客户近期聊天记录"""

    msgs = store_client.list_last_wxqy_msg(external_id, follow_user_id)

    chat_history = ChatHistory()

    if not msgs:
        return chat_history

    for msg in msgs:
        from_id = msg.get("from_id")
        content = msg.get("content")
        msg_time = timestamp2datetime(msg.get("msg_time"))
        if from_id == follow_user_id:
            sender = "客服坐席"
            receiver = "客户"
        else:
            sender = "客户"
            receiver = "客服坐席"

        chat_history.chat_msgs.append(ChatMessage(sender=sender, receiver=receiver, content=content, msg_time=msg_time))

    return chat_history


@debug
def get_kf_history(external_id: str) -> KFChatHistory:
    """获取客户近期微信客服记录"""
    
    msgs = store_client.list_last_wxkf_msg(external_id)
    
    chat_history = KFChatHistory()
    if not msgs:
        return chat_history
    
    for msg in msgs:
        external_id = msg.get("external_id")
        content = msg.get("content")
        msg_time = timestamp2datetime(msg.get("msg_time"))
        
        origin = msg.get("origin")

        if origin == 3:
            sender = "客户"
            receiver = "客服"
        elif origin == 5:
            sender = "客服"
            receiver = "客户"
        else:
            sender = "其他"
            receiver = "其他"
        
        chat_history.kf_chat_msgs.append(ChatMessage(sender=sender, receiver=receiver, content=content, msg_time=msg_time))
    
    return chat_history


@debug
def get_order_history(union_id: str) -> OrderHistory:
    """获取客户订单信息"""
    
    orders = store_client.list_wxxd_order_by_union_id(union_id)

    order_history = OrderHistory()
    if not orders:
        return order_history
    
    for order in orders:
        order_id = order.get("order_id")
        order_products = order.get("order_products")
        order_create_time = timestamp2datetime(order.get("order_create_time"))
        order_history.orders.append(OrderInfo(order_id=order_id, order_products=order_products, order_create_time=order_create_time))
    
    return order_history


@debug
def get_tag_setting() -> TagSetting:
    """获取标签设置"""
    settings = store_client.list_all_tags_setting()
    
    tag_setting = TagSetting()
    if not settings:
        return tag_setting
    
    for setting in settings:
        tag_id = setting.get("tag_id")
        tag_name = setting.get("tag_name")
        tag_setting.tag_setting.append(TagInfo(tag_id=tag_id, tag_name=tag_name))
    
    return tag_setting