from typing import List, Dict, Any
from pydantic import BaseModel, Field
from typing_extensions import Annotated
from models.ecom_base_model import EcomBaseModel

# 使用 Annotated 类型约束
TimeStr = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')]

#####  员工  #####
class EmployeeInfo(EcomBaseModel):
    ''' 员工信息 '''
    user_id: str = Field(default="", description="用户ID")
    name: str = Field(default="", description="名称")


#####  标签  #####
class TagInfo(EcomBaseModel):
    ''' 标签信息 '''
    tag_id: str = Field(default="", description="标签ID")
    tag_name: str = Field(default="", description="标签名称")
    tag_reason: str = Field(default="", description="标签建议原因")

class TagSetting(EcomBaseModel):
    ''' 全局标签设置 '''
    tag_setting: List[TagInfo] = Field(default=[], description="全局标签列表")

class TagSuggestion(EcomBaseModel):
    ''' 标签建议 '''
    tag_ids_add: List[TagInfo] = Field(default=[], description="建议添加的标签列表")
    tag_ids_remove: List[TagInfo] = Field(default=[], description="建议删除的标签列表")

    @classmethod
    def get_example_instance(cls):
        return cls(
            tag_ids_add=[
                    TagInfo(tag_id="tag_id_1", tag_name="tag_name_1", tag_reason="建议添加该标签的原因"),
                    TagInfo(tag_id="tag_id_2", tag_name="tag_name_2", tag_reason="建议添加该标签的原因")
                ],
            tag_ids_remove=[
                    TagInfo(tag_id="tag_id_3", tag_name="tag_name_3", tag_reason="建议删除该标签的原因"),
                    TagInfo(tag_id="tag_id_4", tag_name="tag_name_4", tag_reason="建议删除该标签的原因")
                ]
        )


#####  profile  #####
class ProfileItem(EcomBaseModel):
    ''' profile子项 '''
    item_name: str = Field(default="", description="profile子项的名称")
    item_value: str = Field(default="", description="profile子项的值")


#####  客户  #####
class CustomerInfo(EcomBaseModel):
    ''' 客户信息 '''
    external_id: str = Field(default="", description="外部用户ID")
    union_id: str = Field(default="", description="union_id")
    follow_user_id: str = Field(default="", description="关注用户ID")
    nick_name: str = Field(default="", description="昵称")

class CustomerTags(EcomBaseModel):
    ''' 客户标签 '''
    customer_tags: List[TagInfo] = Field(default=[], description="客户标签")

class CustomerProfile(EcomBaseModel):
    ''' 客户 profile '''
    profile_items: List[ProfileItem] = Field(default=[], description="profile子项的列表")

    @classmethod
    def get_example_instance(cls):
        return cls(
            profile_items=[
                ProfileItem(item_name="会员昵称", item_value="李女士"),
                ProfileItem(item_name="年龄段", item_value="35-40岁"),
                ProfileItem(item_name="性别", item_value="女"),
                ProfileItem(item_name="职业/身份", item_value="企业行政"),
                ProfileItem(item_name="常居城市", item_value="杭州"),
                ProfileItem(item_name="兴趣爱好", item_value="家居收纳、烘焙、健身"),
                ProfileItem(item_name="家庭结构", item_value="三口之家，有1名学龄子女"),
                ProfileItem(item_name="消费能力层级", item_value="中高，注重品质与性价比平衡"),
                ProfileItem(item_name="价格敏感度", item_value="中等，会等大促并主动询问价保"),
                ProfileItem(item_name="偏好品类", item_value="家纺、小家电、食品礼盒、童装"),
                ProfileItem(item_name="常购品牌", item_value="偏好国产中高端品牌"),
                ProfileItem(item_name="尺码/规格偏好", item_value="女装L码，家纺1.8m床品"),
                ProfileItem(item_name="购买频次", item_value="月均2-3单，大促期间明显增加"),
                ProfileItem(item_name="客单价区间", item_value="300-2000元"),
                ProfileItem(item_name="下单渠道偏好", item_value="微信小店、企业微信导购代下单"),
                ProfileItem(item_name="促销活动偏好", item_value="满减、会员券、以旧换新、限时秒杀"),
                ProfileItem(item_name="物流时效要求", item_value="较为关注，希望3天内送达"),
                ProfileItem(item_name="售后服务倾向", item_value="理性沟通，重视处理时效与补偿方案"),
                ProfileItem(item_name="退换货倾向", item_value="偶有尺码不合适退换，流程配合度高"),
                ProfileItem(item_name="会员等级与积分关注", item_value="关注会员价与积分兑换"),
                ProfileItem(item_name="内容与互动偏好", item_value="喜欢看实拍细节图与使用测评"),
                ProfileItem(item_name="沟通风格偏好", item_value="直接高效，偏好文字沟通"),
                ProfileItem(item_name="决策关注点", item_value="品质、售后保障、价格是否最低"),
                ProfileItem(item_name="复购与续卡意愿", item_value="复购意愿强，愿意续费会员"),
                ProfileItem(item_name="流失风险", item_value="低，售后满意度较高")
            ]
        )


#####  聊天对话  #####
class ChatMessage(EcomBaseModel):
    ''' 聊天消息 '''
    sender: str = Field(default="", description="发送者")
    receiver: str = Field(default="", description="接收者")
    content: str = Field(default="", description="内容")
    msg_time: TimeStr = Field(default="", description="消息时间")

class ReplySuggestion(EcomBaseModel):
    ''' 回复建议 '''
    reply_content: str = Field(default="", description="回复内容")
    reply_reason: str = Field(default="", description="回复原因")

    @classmethod
    def get_example_instance(cls):
        return cls(
            reply_content="回复内容",
            reply_reason="回复原因"
        )

class ChatHistory(EcomBaseModel):
    ''' 聊天记录 '''
    chat_msgs: List[ChatMessage] = Field(default=[], description="聊天记录")

class KFChatHistory(EcomBaseModel):
    ''' 客服聊天记录 '''
    kf_chat_msgs: List[ChatMessage] = Field(default=[], description="客服聊天记录")

#####  订单  #####
class OrderInfo(EcomBaseModel):
    ''' 订单信息 '''
    order_id: str = Field(default="", description="订单ID")
    order_products: List[str] = Field(default="", description="订单产品")
    order_create_time: TimeStr = Field(default="", description="订单创建时间")

class OrderHistory(EcomBaseModel):
    ''' 订单历史 '''
    orders: List[OrderInfo] = Field(default=[], description="历史订单记录")
    
#####  日程  #####

class ScheduleSuggestion(EcomBaseModel):
    ''' 日程建议 '''
    title: str = Field(default="",title="Title", description="日程标题")
    start_time: TimeStr = Field(default="",title="Start Time", description="开始时间，格式 yyyy-MM-dd HH:mm:ss")
    duration: int = Field(default=30,title="Duration", description="持续时间，单位分钟")
    schedule_reason: str = Field(default="",title="Schedule Reason", description="日程建议原因")

    @classmethod
    def get_example_instance(cls):
        return cls(
            title="日程标题，包含具体事项说明",
            start_time="2025-08-01 10:00:00",
            duration=60,
            schedule_reason="日程建议原因"
        )

#####  讨论  #####

class JustTalkOutput(EcomBaseModel):
    ''' 讨论应答 '''
    just_talk_output: str = Field(default="", description="讨论应答")
    
    @classmethod
    def get_example_instance(cls):
        return cls(
            just_talk_output="输出应答内容"
        )


#####  意图检测  #####
class Intent(EcomBaseModel):
    ''' 意图 '''
    intent_id: str = Field(default="", description="意图ID")
    intent_description: str = Field(default="", description="意图描述")

    @classmethod
    def get_example_instance(cls):
        return cls(
            intent_id="chat_suggestion",
            intent_description="生成导购话术"
        )


class TaskResult(EcomBaseModel):
    task_result: str = Field(default="", description="任务结果")
    task_result_explain: str = Field(default="", description="任务结果解释")
    task_result_code: int = Field(default=1, description="任务结果代码，0: 结果有效，1: 结果无效")

class NodeResult(EcomBaseModel):
    ''' 节点执行 '''
    execute_node_name: str = Field(default="", description="节点名称")
    execute_result_code: int = Field(default=1, description="节点执行结果代码，0: 成功，1: 失败")
    execute_result_msg: str = Field(default="", description="节点执行结果消息")
    execute_exceptions: List[str] = Field(default=[], description="节点执行异常信息")
    