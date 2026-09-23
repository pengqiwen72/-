from typing import Any, Dict, List, Optional
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


#####  订单查询（多 API 编排）  #####

class OrderItemInfo(CsBaseModel):
    ''' 订单商品行 '''
    sku_id: str = Field(default="", description="商品 SKU 编号")
    product_name: str = Field(default="", description="商品名称")
    quantity: int = Field(default=0, description="购买数量")
    unit_price: float = Field(default=0.0, description="单价（元）")

    def render(self) -> str:
        return f"{self.product_name}（{self.sku_id}）×{self.quantity}"


class LogisticsNode(CsBaseModel):
    ''' 物流轨迹节点 '''
    time: str = Field(default="", description="节点时间 yyyy-MM-dd HH:mm:ss")
    status: str = Field(default="", description="节点状态")
    desc: str = Field(default="", description="节点描述")


class LogisticsTrace(CsBaseModel):
    ''' 物流轨迹 '''
    carrier: str = Field(default="", description="承运商")
    tracking_no: str = Field(default="", description="运单号")
    status: str = Field(default="", description="当前状态")
    nodes: List[LogisticsNode] = Field(default_factory=list, description="轨迹节点，按时间正序")


class StockInfo(CsBaseModel):
    ''' 库存 '''
    sku_id: str = Field(default="", description="商品 SKU 编号")
    warehouse: str = Field(default="", description="仓库")
    available: int = Field(default=0, description="可售数量")
    restock_eta: str = Field(default="", description="补货预计，缺货时才有")

    @property
    def in_stock(self) -> bool:
        return self.available > 0

    def render(self) -> str:
        if self.in_stock:
            return f"{self.sku_id}@{self.warehouse}：可售 {self.available} 件"
        eta = f"，{self.restock_eta}" if self.restock_eta else ""
        return f"{self.sku_id}@{self.warehouse}：缺货{eta}"


class OrderQueryResult(CsBaseModel):
    ''' 订单联合查询结果（订单主数据 + 物流轨迹 + 库存）

    一次查询要打三个业务系统，任何一个都可能超时或没有数据。设计上刻意
    **不把「缺一路」当成整体失败**：订单主数据拿到了就先答，缺的部分记进
    `degraded` 并如实告诉客户「物流信息暂时没取到」，而不是整条链路报错。
    少答一部分永远好过什么都不答。
    '''

    order_id: str = Field(default="", description="订单号（归一层已统一格式）")
    status_code: str = Field(default="", description="订单状态码，内部口径 100~700")
    status_text: str = Field(default="", description="订单状态文案")
    customer_name: str = Field(default="", description="客户姓名")
    pay_amount: float = Field(default=0.0, description="实付金额（元）")
    shipping_fee: float = Field(default=0.0, description="运费（元）")
    warehouse: str = Field(default="", description="发货仓")
    created_at: str = Field(default="", description="下单时间 yyyy-MM-dd HH:mm:ss")
    receiver: str = Field(default="", description="收货信息（嵌套地址已扁平化、手机号已脱敏）")
    items: List[OrderItemInfo] = Field(default_factory=list, description="商品明细")
    logistics: Optional[LogisticsTrace] = Field(default=None, description="物流轨迹；没取到为 None")
    stocks: List[StockInfo] = Field(default_factory=list, description="库存；没取到为空列表")
    degraded: List[str] = Field(default_factory=list, description="降级说明：哪一路没取到、为什么")
    normalize_trace: List[str] = Field(default_factory=list, description="字段归一轨迹，逐条列出上游字段与标准字段的差异")
    summary: str = Field(default="", description="给人看的一句话结论")
    candidates: List[str] = Field(default_factory=list, description="客户名下有多个订单时，可选的订单号（用于反问澄清）")

    def render(self) -> str:
        """把结构化结果渲染成客服可读的答复。"""
        parts: List[str] = [
            f"订单 {self.order_id}（{self.customer_name}）当前状态：{self.status_text}",
            f"商品：{'；'.join(i.render() for i in self.items) or '—'}",
            f"实付 {self.pay_amount:.2f} 元（含运费 {self.shipping_fee:.2f} 元），下单时间 {self.created_at}",
        ]

        if self.logistics is not None:
            trace = self.logistics
            parts.append(f"物流：{trace.carrier} {trace.tracking_no}，当前 {trace.status}")
            for node in trace.nodes[-2:]:
                parts.append(f"  {node.time} {node.desc}")

        if self.stocks:
            parts.append("库存：" + "；".join(s.render() for s in self.stocks[:2]))

        if self.degraded:
            parts.append("（" + "；".join(self.degraded) + "）")

        return "\n".join(p for p in parts if p)


#####  售后处理（政策引擎 + HITL + 幂等）  #####

class AfterSalePlan(CsBaseModel):
    ''' 售后处理方案

    `plan_id` 由 `order_id + action_type` 确定性生成（uuid5），不随调用次数变化 ——
    这是整个幂等链路的锚点：重放、重试、多轮审批都指向同一个 plan_id，
    下游据此判断「这一单我是不是已经处理过了」。
    '''

    plan_id: str = Field(default="", description="方案 ID，order_id+action_type 确定性生成")
    order_id: str = Field(default="", description="订单号")
    action_type: str = Field(default="", description="auto_refund / return_refund / exchange / compensate / reject / handoff")
    refund_type: str = Field(default="", description="处理方式的中文说法")
    policy_id: str = Field(default="", description="命中的政策编号")
    policy_title: str = Field(default="", description="命中的政策标题")
    priority: int = Field(default=0, description="命中政策的优先级")
    amount: float = Field(default=0.0, description="涉及金额（元）")
    need_approval: bool = Field(default=False, description="是否需要人工审批")
    sla: str = Field(default="", description="时效承诺")
    reason: str = Field(default="", description="渲染后的处理依据（给客户看的话术）")
    reason_trace: List[str] = Field(default_factory=list, description="逐条列出哪个条件被什么值命中，解释「为什么是这个结论」")
    status: str = Field(default="matched", description="matched: 已定方案；pending_approval: 等审批；approved/rejected: 审批结论；executed: 已执行；replayed: 幂等重放；conflict: 幂等冲突")
    approved_by: str = Field(default="", description="审批人")
    refund_id: str = Field(default="", description="退款单号；未执行时为空")
    replayed: bool = Field(default=False, description="是否命中了幂等（本次没有真的再发一次退款）")
    message: str = Field(default="", description="给客户/坐席的一句话结论")

    @property
    def is_handoff(self) -> bool:
        return self.action_type == "handoff"

    @property
    def is_reject(self) -> bool:
        return self.action_type == "reject"

    def render(self) -> str:
        head = f"【{self.policy_title}】{self.message}"
        lines = [head, self.reason]
        if self.sla:
            lines.append(f"时效：{self.sla}")
        if self.refund_id:
            lines.append(f"退款单号：{self.refund_id}")
        return "\n".join(p for p in lines if p)


#####  数据周报  #####

class ReportMetric(CsBaseModel):
    ''' 周报指标 '''
    name: str = Field(default="", description="指标名")
    current: float = Field(default=0.0, description="本周值")
    previous: float = Field(default=0.0, description="上周值")
    delta: float = Field(default=0.0, description="环比增量")
    delta_pct: Optional[float] = Field(default=None, description="环比百分比；基数为 0 时为 None，避免出现「+∞%」")
    unit: str = Field(default="", description="单位")

    def render(self) -> str:
        if self.delta_pct is None:
            trend = "（上周无数据）"
        else:
            arrow = "↑" if self.delta_pct > 0 else ("↓" if self.delta_pct < 0 else "→")
            trend = f"{arrow} {abs(self.delta_pct):.1f}%"
        return f"{self.name}：{self.current:g}{self.unit}（上周 {self.previous:g}{self.unit}，{trend}）"


class ReportAnomaly(CsBaseModel):
    ''' 周报异常提示 '''
    metric: str = Field(default="", description="相关指标")
    level: str = Field(default="info", description="warn / info")
    detail: str = Field(default="", description="异常说明")

    def render(self) -> str:
        mark = "⚠" if self.level == "warn" else "·"
        return f"{mark} {self.detail}"


class ReportNarrative(CsBaseModel):
    ''' 周报的**文字部分**（模型只负责这一块）

    数值全部由 `sub_weekly_report` 用 pandas 算好，模型只把它们组织成人话。
    这样切分的理由：同环比算错是**确定性错误**，写个断言就能发现；措辞好坏
    无法自动校验。把模型的输出面收窄到「不涉及算术的那些字」，它就不可能算错 ——
    控制幻觉最有效的办法不是提醒它别说错，而是让它没机会说错。

    生成的文字同样要过 `verify_no_new_numbers`：摘要里出现的每个数字都必须
    能在指标表里找到。模型因此可以自由遣词，但不能凭空造一个「增长 3 倍」。
    '''

    summary: str = Field(default="", description="一段话总结本周运营情况")
    highlights: List[str] = Field(default_factory=list, description="值得关注的要点，每条一句话")

    @classmethod
    def get_example_instance(cls):
        return cls(
            summary="本周共成交 4 单，成交金额 1700.00 元，较上周上升。",
            highlights=["退款率 0.0%，处于健康区间", "在途订单 1 单，需关注物流时效"],
        )


class WeeklyReport(CsBaseModel):
    ''' 运营数据周报 '''
    period_start: str = Field(default="", description="统计周期起 yyyy-MM-dd")
    period_end: str = Field(default="", description="统计周期止 yyyy-MM-dd")
    metrics: List[ReportMetric] = Field(default_factory=list, description="核心指标及环比")
    anomalies: List[ReportAnomaly] = Field(default_factory=list, description="异常提示")
    highlights: List[str] = Field(default_factory=list, description="值得关注的要点")
    summary: str = Field(default="", description="一段话总结")
    table: str = Field(default="", description="Markdown 表格，后台与终端直接渲染")
    degraded: bool = Field(default=False, description="是否走了纯 Python 兜底路径（环境里没有 pandas）")

    def render(self) -> str:
        lines = [f"【{self.period_start} ~ {self.period_end} 运营周报】"]
        if self.table:
            lines.append(self.table)
        if self.anomalies:
            lines.append("异常提示：")
            lines.extend("  " + a.render() for a in self.anomalies)
        if self.summary:
            lines.append(self.summary)
        return "\n".join(lines)


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
    