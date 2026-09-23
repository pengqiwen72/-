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
        """示例实例 = 画像的**字段口径基准**。

        25 个条目分四组：基础属性（1-6）、消费特征（7-13）、偏好（14-17）、
        服务敏感度（18-22）。这个列表同时被两处消费：
          * `get_example_json()` 注入提示词，作为模型输出时的字段参考；
          * 离线合成器 `synthesize_profile` 以它为骨架，只填有证据的项。
        改这里等于改画像口径，两处会同步生效。
        """
        return cls(
            profile_items=[
                # ---- 基础属性 ----
                ProfileItem(item_name="客户姓名", item_value="李女士"),
                ProfileItem(item_name="客户性别", item_value="女"),
                ProfileItem(item_name="年龄段", item_value="30-35 岁"),
                ProfileItem(item_name="所在城市", item_value="浙江省杭州市西湖区"),
                ProfileItem(item_name="职业", item_value="互联网公司产品经理"),
                ProfileItem(item_name="家庭成员", item_value="已婚，育有一子（小学三年级）"),
                # ---- 消费特征 ----
                ProfileItem(item_name="消费能力", item_value="中高，注重品质而非低价"),
                ProfileItem(item_name="客单价区间", item_value="200-600 元"),
                ProfileItem(item_name="购买频次", item_value="月均 2-3 单，大促期间集中下单"),
                ProfileItem(item_name="累计消费金额", item_value="约 4800 元"),
                ProfileItem(item_name="下单渠道", item_value="企业微信私聊、小程序商城"),
                ProfileItem(item_name="支付方式偏好", item_value="微信支付"),
                ProfileItem(item_name="常用收货地址", item_value="杭州市西湖区文三路（工作地址，工作日全天可收）"),
                # ---- 偏好 ----
                ProfileItem(item_name="品类偏好", item_value="家居日用、数码配件、杯壶"),
                ProfileItem(item_name="品牌倾向", item_value="偏好有明确质保承诺的品牌，不排斥新品牌"),
                ProfileItem(item_name="价格敏感度", item_value="中等偏低，更看重售后保障与发货时效"),
                ProfileItem(item_name="促销参与度", item_value="对满减与赠品敏感，大促期间下单集中"),
                # ---- 服务敏感度 ----
                ProfileItem(item_name="退换货倾向", item_value="较低，非必要不退货；出现质量问题会明确主张权益"),
                ProfileItem(item_name="历史售后记录", item_value="近 30 天 1 次：电热毯控制器失灵，已全额退款"),
                ProfileItem(item_name="对售后时效要求", item_value="高，期望 24 小时内给出明确处理结论"),
                ProfileItem(item_name="沟通风格", item_value="直接、目标明确，不喜欢反复确认"),
                ProfileItem(item_name="关注点", item_value="发货时效、质保范围、破损赔付流程"),
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


#####  商品咨询问答（RAG）  #####

class ProductQACitation(CsBaseModel):
    ''' 商品问答出处 '''
    index: int = Field(default=0, description="出处编号，与回答正文里的【出处N】对应")
    doc_title: str = Field(default="", description="出处文档标题")
    section: str = Field(default="", description="出处章节路径")
    source: str = Field(default="", description="出处文件相对路径")
    snippet: str = Field(default="", description="出处原文片段")
    score: float = Field(default=0.0, description="重排相关度，[0,1)")

    def render(self) -> str:
        from utils.text import citation_path

        located = citation_path(self.doc_title, self.section)
        return (
            f"【出处{self.index}】{located}"
            f"（{self.source}，相关度 {self.score:.2f}）\n    {self.snippet}"
        )


class ProductQA(CsBaseModel):
    ''' 商品咨询问答结果

    ⚠️ 这里刻意**不直接复用** `rag.types.RagAnswer`：那个是 dataclass，只服务于
    进程内的检索链路。而问答结果要写进 LangGraph 状态、随检查点序列化、
    并被 webapp 的接口层直接 `model_dump()` 出去 —— 跨进程边界的数据必须是
    pydantic 模型，否则反序列化时拿到的是一个裸 dict，下游 `getattr` 全失效。
    两个模型之间用 `from_rag_answer()` 单向转换，字段一一对应。
    '''

    question: str = Field(default="", description="客户原始问题")
    answer: str = Field(default="", description="回答正文")
    confidence: float = Field(default=0.0, description="置信度，[0,1]")
    status: str = Field(default="answered", description="answered: 已作答；handoff: 转人工")
    reason: str = Field(default="", description="转人工原因；正常作答时为空")
    citations: List[ProductQACitation] = Field(default_factory=list, description="出处列表")
    matched_terms: List[str] = Field(default_factory=list, description="命中的实词，用于解释这次靠什么命中")
    degraded: bool = Field(default=False, description="是否发生过降级（模型输出被数字校验拦下、回退为事实转述）")

    @classmethod
    def from_rag_answer(cls, question: str, rag_answer) -> "ProductQA":
        """把 `rag.types.RagAnswer` 转成可入状态的模型。"""
        return cls(
            question=question,
            answer=rag_answer.answer,
            confidence=round(float(rag_answer.confidence), 4),
            status=rag_answer.status,
            reason=rag_answer.reason,
            citations=[
                ProductQACitation(
                    index=c.index,
                    doc_title=c.title,
                    section=c.section,
                    source=c.source,
                    snippet=c.snippet,
                    score=round(float(c.score), 4),
                )
                for c in rag_answer.citations
            ],
            matched_terms=list(rag_answer.matched_terms),
            degraded=bool(rag_answer.degraded),
        )

    @property
    def is_handoff(self) -> bool:
        return self.status == "handoff"

    def citation_footer(self) -> str:
        if not self.citations:
            return ""
        return "参考资料：\n" + "\n".join(c.render() for c in self.citations)

    def render(self) -> str:
        """正文 + 出处页脚，给人看的完整回答。"""
        parts = [self.answer.strip()]
        footer = self.citation_footer()
        if footer:
            parts.append(footer)
        return "\n\n".join(p for p in parts if p)


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
    