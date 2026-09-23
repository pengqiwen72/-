from typing import List, Dict, Any
from pydantic import BaseModel, Field
from typing_extensions import Annotated
from models.cs_base_model import CsBaseModel

# 使用 Annotated 类型约束
TimeStr = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')]

#####  员工  #####
class EmployeeInfo(CsBaseModel):
    ''' 员工信息 '''
    user_id: str = Field(default="", description="用户ID")
    name: str = Field(default="", description="名称")


#####  标签  #####
class TagInfo(CsBaseModel):
    ''' 标签信息 '''
    tag_id: str = Field(default="", description="标签ID")
    tag_name: str = Field(default="", description="标签名称")
    tag_reason: str = Field(default="", description="标签建议原因")

class TagSetting(CsBaseModel):
    ''' 全局标签设置 '''
    tag_setting: List[TagInfo] = Field(default=[], description="全局标签列表")

class TagSuggestion(CsBaseModel):
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
class ProfileItem(CsBaseModel):
    ''' profile子项 '''
    item_name: str = Field(default="", description="profile子项的名称")
    item_value: str = Field(default="", description="profile子项的值")


#####  客户  #####
class CustomerInfo(CsBaseModel):
    ''' 客户信息 '''
    external_id: str = Field(default="", description="外部用户ID")
    union_id: str = Field(default="", description="union_id")
    follow_user_id: str = Field(default="", description="关注用户ID")
    nick_name: str = Field(default="", description="昵称")

class CustomerTags(CsBaseModel):
    ''' 客户标签 '''
    customer_tags: List[TagInfo] = Field(default=[], description="客户标签")

class CustomerProfile(CsBaseModel):
    ''' 客户 profile '''
    profile_items: List[ProfileItem] = Field(default=[], description="profile子项的列表")

    @classmethod
    def get_example_instance(cls):
        return cls(
            profile_items=[
                ProfileItem(item_name="家长姓名", item_value="李女士"),
                ProfileItem(item_name="家长年龄", item_value="38"),
                ProfileItem(item_name="家长性别", item_value="女"),
                ProfileItem(item_name="家长职业", item_value="企业行政"),
                ProfileItem(item_name="家长兴趣爱好", item_value="育儿交流、阅读、健身"),
                ProfileItem(item_name="婚姻状况", item_value="已婚"),
                ProfileItem(item_name="家长教育程度", item_value="本科"),
                ProfileItem(item_name="学生学段年级", item_value="小学六年级"),
                ProfileItem(item_name="学生性别", item_value="男"),
                ProfileItem(item_name="学生学习情况", item_value="成绩中等，数学偏薄弱"),
                ProfileItem(item_name="薄弱学科", item_value="数学"),
                ProfileItem(item_name="优势学科", item_value="语文、英语"),
                ProfileItem(item_name="学习目标", item_value="冲刺重点初中"),
                ProfileItem(item_name="报课类型偏好", item_value="全科培优、小升初冲刺班"),
                ProfileItem(item_name="上课形式偏好", item_value="线下面授"),
                ProfileItem(item_name="上课时间偏好", item_value="周末白天、寒暑假集训"),
                ProfileItem(item_name="购课消费频率", item_value="按学期持续购课、常年续费"),
                ProfileItem(item_name="单次购课大致金额", item_value="3000-5000元"),
                ProfileItem(item_name="教辅资料采购习惯", item_value="按需购买，优先选购真题试卷"),
                ProfileItem(item_name="购课/购资料渠道", item_value="线下门店、企业微信、线上小程序"),
                ProfileItem(item_name="家庭成员", item_value="三口之家，独生子，无老人同住"),
                ProfileItem(item_name="家长育儿状态", item_value="双职工，晚间陪伴学习"),
                ProfileItem(item_name="家校沟通频率", item_value="沟通较频繁"),
                ProfileItem(item_name="服务关注点", item_value="师资水平、学情报告、课后答疑、课时预留"),
                ProfileItem(item_name="课程续费意愿", item_value="续费意愿强烈")
            ]
        )


#####  聊天对话  #####
class ChatMessage(CsBaseModel):
    ''' 聊天消息 '''
    sender: str = Field(default="", description="发送者")
    receiver: str = Field(default="", description="接收者")
    content: str = Field(default="", description="内容")
    msg_time: TimeStr = Field(default="", description="消息时间")

class ReplySuggestion(CsBaseModel):
    ''' 回复建议 '''
    reply_content: str = Field(default="", description="回复内容")
    reply_reason: str = Field(default="", description="回复原因")

    @classmethod
    def get_example_instance(cls):
        return cls(
            reply_content="回复内容",
            reply_reason="回复原因"
        )

class ChatHistory(CsBaseModel):
    ''' 聊天记录 '''
    chat_msgs: List[ChatMessage] = Field(default=[], description="聊天记录")

class KFChatHistory(CsBaseModel):
    ''' 客服聊天记录 '''
    kf_chat_msgs: List[ChatMessage] = Field(default=[], description="客服聊天记录")

#####  订单  #####
class OrderInfo(CsBaseModel):
    ''' 订单信息 '''
    order_id: str = Field(default="", description="订单ID")
    order_products: List[str] = Field(default_factory=list, description="订单产品")
    order_create_time: TimeStr = Field(default="", description="订单创建时间")

class OrderHistory(CsBaseModel):
    ''' 订单历史 '''
    orders: List[OrderInfo] = Field(default=[], description="历史订单记录")
    
#####  日程  #####

class ScheduleSuggestion(CsBaseModel):
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

class JustTalkOutput(CsBaseModel):
    ''' 讨论应答 '''
    just_talk_output: str = Field(default="", description="讨论应答")
    
    @classmethod
    def get_example_instance(cls):
        return cls(
            just_talk_output="输出应答内容"
        )


#####  意图检测  #####
class Intent(CsBaseModel):
    ''' 意图 '''
    intent_id: str = Field(default="", description="意图ID")
    intent_description: str = Field(default="", description="意图描述")

    @classmethod
    def get_example_instance(cls):
        return cls(
            intent_id="chat_suggestion",
            intent_description="生成聊天建议"
        )


class TaskResult(CsBaseModel):
    task_result: str = Field(default="", description="任务结果")
    task_result_explain: str = Field(default="", description="任务结果解释")
    task_result_code: int = Field(default=1, description="任务结果代码，0: 结果有效，1: 结果无效")

class NodeResult(CsBaseModel):
    ''' 节点执行 '''
    execute_node_name: str = Field(default="", description="节点名称")
    execute_result_code: int = Field(default=1, description="节点执行结果代码，0: 成功，1: 失败")
    execute_result_msg: str = Field(default="", description="节点执行结果消息")
    execute_exceptions: List[str] = Field(default=[], description="节点执行异常信息")
    