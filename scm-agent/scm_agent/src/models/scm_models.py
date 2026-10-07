from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from typing_extensions import Annotated
from models.scm_base_model import ScmBaseModel

# 使用 Annotated 类型约束
TimeStr = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')]

#####  员工  #####
class EmployeeInfo(ScmBaseModel):
    ''' 员工信息 '''
    user_id: str = Field(default="", description="用户ID")
    name: str = Field(default="", description="名称")


#####  标签  #####
class TagInfo(ScmBaseModel):
    ''' 标签信息 '''
    tag_id: str = Field(default="", description="标签ID")
    tag_name: str = Field(default="", description="标签名称")
    tag_reason: str = Field(default="", description="标签建议原因")

class TagSetting(ScmBaseModel):
    ''' 全局标签设置 '''
    tag_setting: List[TagInfo] = Field(default=[], description="全局标签列表")

class TagSuggestion(ScmBaseModel):
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
class ProfileItem(ScmBaseModel):
    ''' profile子项 '''
    item_name: str = Field(default="", description="profile子项的名称")
    item_value: str = Field(default="", description="profile子项的值")


#####  客户  #####
class CustomerInfo(ScmBaseModel):
    ''' 客户信息 '''
    external_id: str = Field(default="", description="外部用户ID")
    union_id: str = Field(default="", description="union_id")
    follow_user_id: str = Field(default="", description="关注用户ID")
    nick_name: str = Field(default="", description="昵称")

class CustomerTags(ScmBaseModel):
    ''' 客户标签 '''
    customer_tags: List[TagInfo] = Field(default=[], description="客户标签")

class CustomerProfile(ScmBaseModel):
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
class ChatMessage(ScmBaseModel):
    ''' 聊天消息 '''
    sender: str = Field(default="", description="发送者")
    receiver: str = Field(default="", description="接收者")
    content: str = Field(default="", description="内容")
    msg_time: TimeStr = Field(default="", description="消息时间")

class ReplySuggestion(ScmBaseModel):
    ''' 回复建议 '''
    reply_content: str = Field(default="", description="回复内容")
    reply_reason: str = Field(default="", description="回复原因")

    @classmethod
    def get_example_instance(cls):
        return cls(
            reply_content="回复内容",
            reply_reason="回复原因"
        )

class ChatHistory(ScmBaseModel):
    ''' 聊天记录 '''
    chat_msgs: List[ChatMessage] = Field(default=[], description="聊天记录")

class KFChatHistory(ScmBaseModel):
    ''' 客服聊天记录 '''
    kf_chat_msgs: List[ChatMessage] = Field(default=[], description="客服聊天记录")

#####  订单  #####
class OrderInfo(ScmBaseModel):
    ''' 订单信息 '''
    order_id: str = Field(default="", description="订单ID")
    order_products: List[str] = Field(default_factory=list, description="订单产品")
    order_create_time: TimeStr = Field(default="", description="订单创建时间")

class OrderHistory(ScmBaseModel):
    ''' 订单历史 '''
    orders: List[OrderInfo] = Field(default=[], description="历史订单记录")
    
#####  日程  #####

class ScheduleSuggestion(ScmBaseModel):
    ''' 日程建议 '''
    title: str = Field(default="",title="Title", description="日程标题")
    #: ⚠️ pattern 特意允许空串，别「收紧」回 TimeStr。离线合成器用
    #: 「title / start_time 同时为空」表达「没有日程建议」（见 `_empty_schedule`），
    #: 而 pydantic 只校验**显式传入**的值、不校验默认值 —— 收紧后默认值 "" 照样能
    #: 构造，但 `model_validate({"start_time": ""})` 会 ValidationError，合成结果
    #: 被 registry 静默回退成示例实例（一条假日程还能通过下游节点的非空校验被真建出来）。
    #: 下游 `create_schedule_node` 本就判空跳过，放开空串与它对齐。
    start_time: Annotated[str, Field(pattern=r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})?$')] = Field(default="",title="Start Time", description="开始时间，格式 yyyy-MM-dd HH:mm:ss；没有日程建议时为空")
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

class JustTalkOutput(ScmBaseModel):
    ''' 讨论应答 '''
    just_talk_output: str = Field(default="", description="讨论应答")
    
    @classmethod
    def get_example_instance(cls):
        return cls(
            just_talk_output="输出应答内容"
        )


#####  商品咨询问答（RAG）  #####

class ProductQACitation(ScmBaseModel):
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


class ProductQA(ScmBaseModel):
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

class OrderItemInfo(ScmBaseModel):
    ''' 订单商品行 '''
    sku_id: str = Field(default="", description="商品 SKU 编号")
    product_name: str = Field(default="", description="商品名称")
    quantity: int = Field(default=0, description="购买数量")
    unit_price: float = Field(default=0.0, description="单价（元）")

    def render(self) -> str:
        return f"{self.product_name}（{self.sku_id}）×{self.quantity}"


class LogisticsNode(ScmBaseModel):
    ''' 物流轨迹节点 '''
    time: str = Field(default="", description="节点时间 yyyy-MM-dd HH:mm:ss")
    status: str = Field(default="", description="节点状态")
    desc: str = Field(default="", description="节点描述")


class LogisticsTrace(ScmBaseModel):
    ''' 物流轨迹 '''
    carrier: str = Field(default="", description="承运商")
    tracking_no: str = Field(default="", description="运单号")
    status: str = Field(default="", description="当前状态")
    nodes: List[LogisticsNode] = Field(default_factory=list, description="轨迹节点，按时间正序")


class StockInfo(ScmBaseModel):
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


class OrderQueryResult(ScmBaseModel):
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

class AfterSalePlan(ScmBaseModel):
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

class ReportMetric(ScmBaseModel):
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


class ReportAnomaly(ScmBaseModel):
    ''' 周报异常提示 '''
    metric: str = Field(default="", description="相关指标")
    level: str = Field(default="info", description="warn / info")
    detail: str = Field(default="", description="异常说明")

    def render(self) -> str:
        mark = "⚠" if self.level == "warn" else "·"
        return f"{mark} {self.detail}"


class ReportNarrative(ScmBaseModel):
    ''' 周报的**文字部分**（模型只负责这一块）

    数值全部由 `sub_weekly_report` 的 aggregate 节点用确定性代码算好，
    模型只把它们组织成人话。这样切分的理由：同环比算错是**确定性错误**，
    写个断言就能发现；措辞好坏无法自动校验。把模型的输出面收窄到
    「不涉及算术的那些字」，它就不可能算错 ——
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


class WeeklyReport(ScmBaseModel):
    ''' 运营数据周报 '''
    period_start: str = Field(default="", description="统计周期起 yyyy-MM-dd")
    period_end: str = Field(default="", description="统计周期止 yyyy-MM-dd")
    metrics: List[ReportMetric] = Field(default_factory=list, description="核心指标及环比")
    anomalies: List[ReportAnomaly] = Field(default_factory=list, description="异常提示")
    highlights: List[str] = Field(default_factory=list, description="值得关注的要点")
    summary: str = Field(default="", description="一段话总结")
    table: str = Field(default="", description="Markdown 表格，后台与终端直接渲染")
    degraded: bool = Field(
        default=False,
        description="是否走了兜底：指标没算出来，或模型摘要未通过数字校验而回退为确定性转述",
    )

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
class Intent(ScmBaseModel):
    ''' 意图 '''
    intent_id: str = Field(default="", description="意图ID")
    intent_description: str = Field(default="", description="意图描述")

    @classmethod
    def get_example_instance(cls):
        return cls(
            intent_id="chat_suggestion",
            intent_description="生成聊天建议"
        )


class TaskResult(ScmBaseModel):
    task_result: str = Field(default="", description="任务结果")
    task_result_explain: str = Field(default="", description="任务结果解释")
    task_result_code: int = Field(default=1, description="任务结果代码，0: 结果有效，1: 结果无效")

class NodeResult(ScmBaseModel):
    ''' 节点执行 '''
    execute_node_name: str = Field(default="", description="节点名称")
    execute_result_code: int = Field(default=1, description="节点执行结果代码，0: 成功，1: 失败")
    execute_result_msg: str = Field(default="", description="节点执行结果消息")
    execute_exceptions: List[str] = Field(default=[], description="节点执行异常信息")


# ============================================================================
# 供应链采购仓储域
#
# 以下模型服务于「供应链采购仓储智能系统」的四条能力链路：
#   智能问答（制度 RAG + 图谱多跳）、采购执行（申请审批 / 收货质检）、
#   库存管理、供应商管理。
#
# 数据形状与 data/seed/*.json 一一对应；store/seed_loader.py 的 _verify
# 按这里的字段口径做跨文件完整性校验（外键 / 金额自洽 / 报价对存在）。
# ============================================================================


#####  主数据：物料 / 供应商 / 仓库  #####

class MaterialInfo(ScmBaseModel):
    ''' 物料主数据 '''
    material_code: str = Field(default="", description="物料编码，形如 M100023")
    material_name: str = Field(default="", description="物料名称")
    spec: str = Field(default="", description="规格描述")
    category: str = Field(default="", description="物料分类")
    unit: str = Field(default="", description="计量单位（kg / 支 / 个 / 米 …）")
    unit_price_ref: float = Field(default=0.0, description="参考单价（元）")
    substitutes: List[str] = Field(
        default_factory=list,
        description="替代料编码列表；图谱 SUBSTITUTES 边的唯一来源",
    )

    @classmethod
    def get_example_instance(cls):
        return cls(
            material_code="M100023",
            material_name="冷轧钢板 1.2mm",
            spec="1.2mm×1250mm×C",
            category="金属原材料",
            unit="kg",
            unit_price_ref=5.6,
            substitutes=["M100031"],
        )


class SupplierInfo(ScmBaseModel):
    ''' 供应商主数据 '''
    supplier_code: str = Field(default="", description="供应商编码，形如 S001")
    supplier_name: str = Field(default="", description="供应商名称")
    is_new: bool = Field(default=False, description="是否新供应商（未合作过，首单需审批）")
    lead_time_days: int = Field(default=0, description="承诺交期（天）")
    on_time_rate: float = Field(default=0.0, description="历史准时交付率，[0,1]")
    defect_rate: float = Field(default=0.0, description="历史到货不良率，[0,1]")
    cooperation_since: str = Field(default="", description="合作起始月份（YYYY-MM）；新供应商为空")
    main_categories: List[str] = Field(default_factory=list, description="主营品类")


class WarehouseInfo(ScmBaseModel):
    ''' 仓库主数据 '''
    warehouse_code: str = Field(default="", description="仓库编码，形如 WH01")
    warehouse_name: str = Field(default="", description="仓库名称")
    city: str = Field(default="", description="所在城市")
    address: str = Field(default="", description="详细地址")


#####  库存  #####

class StockItem(ScmBaseModel):
    ''' 单个物料在单个仓库的库存快照

    三个指标口径（库存子图的聚合节点必须与此一致）：
      * qty_available = on_hand - reserved（可动用库存，预警看的是它而不是在库量）
      * below_safety  = qty_available < safety_stock（安全库存预警）
      * turnover_days = on_hand / avg_daily_out（按近 30 天出库推的可支撑天数）
    '''

    material_code: str = Field(default="", description="物料编码")
    warehouse_code: str = Field(default="", description="仓库编码")
    on_hand: int = Field(default=0, description="在库数量")
    reserved: int = Field(default=0, description="已预留（占用）数量")
    safety_stock: int = Field(default=0, description="安全库存线")
    avg_daily_out: float = Field(default=0.0, description="近 30 天日均出库量")

    @property
    def qty_available(self) -> int:
        return self.on_hand - self.reserved

    @property
    def below_safety(self) -> bool:
        return self.qty_available < self.safety_stock

    @property
    def turnover_days(self) -> float:
        """按当前在库量推出的可支撑天数；没有出库记录时为 0（不参与展示）。"""
        if self.avg_daily_out <= 0:
            return 0.0
        return round(self.on_hand / self.avg_daily_out, 1)

    def render(self) -> str:
        mark = "（低于安全库存）" if self.below_safety else ""
        return (
            f"{self.warehouse_code}：可用 {self.qty_available}"
            f"（在库 {self.on_hand}，预留 {self.reserved}）{mark}"
        )


#####  报价  #####

class SupplierQuote(ScmBaseModel):
    ''' 供应商报价（物料 × 供应商） '''
    material_code: str = Field(default="", description="物料编码")
    supplier_code: str = Field(default="", description="供应商编码")
    unit_price: float = Field(default=0.0, description="报价单价（元）")
    min_order_qty: int = Field(default=0, description="最小起订量")
    valid_days: int = Field(default=30, description="报价有效天数")


#####  要素提取（模型调用；离线由 llm/offline/synth_scm.py 合成）  #####

class PurchaseDraft(ScmBaseModel):
    ''' 采购申请要素草稿：从自然语言申请里抽出的结构化要素。

    只做「抽取」不做「判断」—— 数量是否超限、供应商要不要审批，全部交给
    确定性的策略引擎（integrations/policy.py）。抽取错一个字段还能澄清，
    策略判错一次就是真金白银，两种错误不能混在同一个组件里。
    '''

    material_query: str = Field(default="", description="物料的口语说法，原样保留（如「冷轧钢板」）")
    material_code: str = Field(default="", description="命中的物料编码；没命中留空，由上层澄清")
    qty: float = Field(default=0.0, description="申请数量")
    unit: str = Field(default="", description="数量单位（kg / 支 / 个 …），吨会换算成 kg")
    target_warehouse: str = Field(default="", description="到货仓库编码")
    supplier_code: str = Field(default="", description="指定供应商编码；未指定留空")
    reason: str = Field(default="", description="提报说明，原样保留")

    @classmethod
    def get_example_instance(cls):
        return cls(
            material_query="冷轧钢板 1.2mm",
            material_code="M100023",
            qty=8000,
            unit="kg",
            target_warehouse="WH01",
            supplier_code="S012",
            reason="产线备料，现有库存低于安全线",
        )


class ReceiptDraft(ScmBaseModel):
    ''' 收货登记要素草稿：从自然语言收货登记里抽出的结构化要素。 '''

    po_no: str = Field(default="", description="采购订单号，形如 PO20260928002")
    material_code: str = Field(default="", description="物料编码；未写明时由 PO 反查")
    qty_received: int = Field(default=0, description="实收数量")
    qty_defect: int = Field(default=0, description="不良数量")
    defect_type: str = Field(default="", description="不良类型（磕碰变形 / 锈蚀 / 尺寸超差 …）")
    warehouse_code: str = Field(default="", description="收货仓库编码")
    description: str = Field(default="", description="登记说明，原样保留")

    @classmethod
    def get_example_instance(cls):
        return cls(
            po_no="PO20260928002",
            material_code="M400301",
            qty_received=920,
            qty_defect=30,
            defect_type="磕碰变形",
            warehouse_code="WH02",
            description="到货外箱破损，开箱抽检发现部分轴承保持架变形",
        )


#####  采购执行结果  #####

class PurchasePlan(ScmBaseModel):
    ''' 采购申请的处理方案（策略结果 + 审批状态）

    `plan_id` 由 `biz_id`（确定性 PR 单号）uuid5 生成，不随调用次数变化 ——
    重放、重试、多轮审批都指向同一个 plan_id，下游据此判断「这单我是不是已经下过了」。
    与售后链路是同一个幂等锚点设计，只是业务单号换成了采购申请号。
    '''

    plan_id: str = Field(default="", description="方案 ID，由 biz_id 确定性生成（uuid5）")
    biz_id: str = Field(default="", description="业务单号：PR 开头的申请单号，由要素确定性生成")
    material_code: str = Field(default="", description="物料编码")
    material_name: str = Field(default="", description="物料名称")
    qty: float = Field(default=0.0, description="申请数量")
    unit: str = Field(default="", description="数量单位")
    unit_price: float = Field(default=0.0, description="单价（元）")
    amount: float = Field(default=0.0, description="申请金额（元）")
    target_warehouse: str = Field(default="", description="到货仓库编码")
    supplier_code: str = Field(default="", description="供应商编码")
    supplier_name: str = Field(default="", description="供应商名称")
    expect_arrival: str = Field(default="", description="预计到货日期 yyyy-MM-dd")
    need_approval: bool = Field(default=False, description="是否需要人工审批")
    policy_id: str = Field(default="", description="命中的采购策略编号")
    policy_title: str = Field(default="", description="命中的采购策略标题")
    priority: int = Field(default=0, description="命中策略的优先级")
    reason: str = Field(default="", description="渲染后的处理依据（审批人看的话术）")
    reason_trace: List[str] = Field(
        default_factory=list, description="逐条列出哪个条件被什么值命中"
    )
    status: str = Field(
        default="matched",
        description="matched / pending_approval / approved / rejected / executed / replayed / conflict",
    )
    approved_by: str = Field(default="", description="审批人")
    replayed: bool = Field(default=False, description="是否命中了幂等（本次没有真的再下过单）")
    operator_id: str = Field(default="", description="提报人")
    description: str = Field(default="", description="申请说明")
    message: str = Field(default="", description="一句话结论")

    def render(self) -> str:
        head = f"【{self.policy_title}】{self.message}"
        lines = [head, self.reason]
        lines.append(
            f"申请：{self.material_name}（{self.material_code}）× {self.qty:g}{self.unit}，"
            f"金额 {self.amount:.2f} 元"
        )
        if self.supplier_code:
            lines.append(f"供应商：{self.supplier_name}（{self.supplier_code}）")
        if self.target_warehouse or self.expect_arrival:
            lines.append(f"到货：{self.target_warehouse} {self.expect_arrival}".rstrip())
        if self.biz_id:
            lines.append(f"申请单号：{self.biz_id}")
        return "\n".join(p for p in lines if p)


class ReceiptPlan(ScmBaseModel):
    ''' 收货质检方案（策略结果 + 复核状态，字段与中断 payload 一一对应）

    `defect_rate` 由 `compute_defect_rate` 统一计算：口径只有一处，
    策略引擎的阈值判定与展示文案读的是同一个值。
    '''

    plan_id: str = Field(default="", description="方案 ID，由 biz_id 确定性生成（uuid5）")
    biz_id: str = Field(default="", description="业务单号：RC 开头的收货单号")
    po_no: str = Field(default="", description="关联的采购订单号")
    material_code: str = Field(default="", description="物料编码")
    material_name: str = Field(default="", description="物料名称")
    warehouse_code: str = Field(default="", description="收货仓库编码")
    supplier_code: str = Field(default="", description="供应商编码")
    supplier_name: str = Field(default="", description="供应商名称")
    qty_expected: int = Field(default=0, description="订单应收数量")
    qty_received: int = Field(default=0, description="实收数量")
    qty_defect: int = Field(default=0, description="不良数量")
    defect_rate: float = Field(default=0.0, description="不良率 = qty_defect / qty_received")
    defect_type: str = Field(default="", description="不良类型")
    disposition: str = Field(default="", description="处置方式：入库 / 让步接收 / 退货 / 换货")
    need_review: bool = Field(default=False, description="是否需要质检复核（不良率超阈值）")
    policy_id: str = Field(default="", description="命中的质检策略编号")
    policy_title: str = Field(default="", description="命中的质检策略标题")
    priority: int = Field(default=0, description="命中策略的优先级")
    reason: str = Field(default="", description="渲染后的处理依据")
    reason_trace: List[str] = Field(default_factory=list, description="逐条命中说明")
    status: str = Field(
        default="matched",
        description="matched / pending_review / approved / rejected / executed / replayed / conflict",
    )
    approved_by: str = Field(default="", description="复核人")
    inspector: str = Field(default="", description="登记人（质检员）")
    replayed: bool = Field(default=False, description="是否命中了幂等")
    message: str = Field(default="", description="一句话结论")

    @staticmethod
    def compute_defect_rate(qty_received: int, qty_defect: int) -> float:
        """不良率口径的唯一定义处；实收为 0 时返回 0（链路会先拦下 0 实收）。"""
        if qty_received <= 0:
            return 0.0
        return round(qty_defect / qty_received, 4)

    def render(self) -> str:
        head = f"【{self.policy_title}】{self.message}"
        lines = [
            head,
            self.reason,
            (
                f"收货：{self.material_name}（{self.material_code}）实收 {self.qty_received}"
                f"，不良 {self.qty_defect}，不良率 {self.defect_rate:.2%}"
            ),
        ]
        if self.disposition:
            lines.append(f"处置：{self.disposition}")
        if self.biz_id:
            lines.append(f"收货单号：{self.biz_id}")
        return "\n".join(p for p in lines if p)


#####  智能问答（制度 RAG + 图谱双通道）  #####

class MaterialQACitation(ScmBaseModel):
    ''' 智能问答出处 '''
    index: int = Field(default=0, description="出处编号，与回答正文里的【出处N】对应")
    doc_title: str = Field(default="", description="出处标题（文档标题或图关系名）")
    section: str = Field(default="", description="出处章节路径")
    source: str = Field(default="", description="出处位置（文档相对路径或图路径）")
    snippet: str = Field(default="", description="出处原文片段")
    score: float = Field(default=0.0, description="重排相关度，[0,1)")

    def render(self) -> str:
        from utils.text import citation_path

        located = citation_path(self.doc_title, self.section)
        return (
            f"【出处{self.index}】{located}"
            f"（{self.source}，相关度 {self.score:.2f}）\n    {self.snippet}"
        )


class MaterialQA(ScmBaseModel):
    ''' 智能问答结果（制度文档 + 图谱关系双通道）

    与 `ProductQA` 同一套结构：跨进程边界的数据必须是 pydantic 模型，
    两个模型之间用 `from_rag_answer()` 单向转换，字段一一对应；
    额外带上 `channel`（DOC / RELATION / BOTH），方便演示时解释这题走的哪条通道。
    '''

    question: str = Field(default="", description="原始问题")
    answer: str = Field(default="", description="回答正文")
    confidence: float = Field(default=0.0, description="置信度，[0,1]")
    status: str = Field(default="answered", description="answered: 已作答；handoff: 转人工")
    reason: str = Field(default="", description="转人工原因；正常作答时为空")
    channel: str = Field(default="DOC", description="本次检索通道：DOC / RELATION / BOTH")
    citations: List[MaterialQACitation] = Field(default_factory=list, description="出处列表")
    matched_terms: List[str] = Field(default_factory=list, description="命中的实词，用于解释这次靠什么命中")
    degraded: bool = Field(default=False, description="是否发生过降级（模型输出被数字校验拦下、回退为事实转述）")

    @classmethod
    def from_rag_answer(cls, question: str, rag_answer) -> "MaterialQA":
        """把 `rag.types.RagAnswer` 转成可入状态的模型。"""
        return cls(
            question=question,
            answer=rag_answer.answer,
            confidence=round(float(rag_answer.confidence), 4),
            status=rag_answer.status,
            reason=rag_answer.reason,
            channel=str(getattr(rag_answer, "channel", "DOC")),
            citations=[
                MaterialQACitation(
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


#####  库存 / 供应商结果  #####

class TransferSuggestion(ScmBaseModel):
    ''' 调拨建议：从有余量的仓调往缺口仓 '''
    material_code: str = Field(default="", description="物料编码")
    from_warehouse: str = Field(default="", description="调出仓库")
    to_warehouse: str = Field(default="", description="调入仓库")
    qty: int = Field(default=0, description="建议调拨数量")
    reason: str = Field(default="", description="建议依据，引用两仓的可用量与安全线")

    def render(self) -> str:
        return f"{self.from_warehouse} → {self.to_warehouse}：调拨 {self.qty}（{self.reason}）"


class InventoryReport(ScmBaseModel):
    ''' 物料库存分布与预警报告 '''
    material_code: str = Field(default="", description="物料编码")
    material_name: str = Field(default="", description="物料名称")
    unit: str = Field(default="", description="计量单位")
    total_on_hand: int = Field(default=0, description="全仓在库合计")
    total_available: int = Field(default=0, description="全仓可用合计")
    safety_stock: int = Field(default=0, description="全仓安全库存合计（各仓求和）")
    below_safety: bool = Field(default=False, description="是否存在低于安全线的仓")
    turnover_days: float = Field(default=0.0, description="全仓口径可支撑天数")
    rows: List[StockItem] = Field(default_factory=list, description="分仓明细")
    transfers: List[TransferSuggestion] = Field(default_factory=list, description="调拨建议")
    summary: str = Field(default="", description="一句话结论")

    def render(self) -> str:
        lines = [f"【{self.material_name}（{self.material_code}）库存】{self.summary}"]
        lines.extend("  " + r.render() for r in self.rows)
        lines.extend("  " + t.render() for t in self.transfers)
        return "\n".join(lines)


class SupplierScore(ScmBaseModel):
    ''' 单个供应商的综合评分（用于比价排名与履约评估） '''
    supplier_code: str = Field(default="", description="供应商编码")
    supplier_name: str = Field(default="", description="供应商名称")
    unit_price: float = Field(default=0.0, description="报价单价（元）")
    lead_time_days: int = Field(default=0, description="承诺交期（天）")
    on_time_rate: float = Field(default=0.0, description="历史准时交付率，[0,1]")
    defect_rate: float = Field(default=0.0, description="历史到货不良率，[0,1]")
    price_score: float = Field(default=0.0, description="价格得分（最低价归一化为 1）")
    score: float = Field(default=0.0, description="综合得分，[0,1]")
    rank: int = Field(default=0, description="名次，1 为最优")

    def render(self) -> str:
        return (
            f"No.{self.rank} {self.supplier_name}（{self.supplier_code}）"
            f"：报价 {self.unit_price:.2f} 元，交期 {self.lead_time_days} 天，"
            f"准时率 {self.on_time_rate:.0%}，不良率 {self.defect_rate:.2%}，综合得分 {self.score:.3f}"
        )


class SupplierComparison(ScmBaseModel):
    ''' 物料比价 / 供应商排名结果 '''
    material_code: str = Field(default="", description="物料编码")
    material_name: str = Field(default="", description="物料名称")
    unit: str = Field(default="", description="计量单位")
    best_supplier_code: str = Field(default="", description="综合得分最高的供应商编码")
    scores: List[SupplierScore] = Field(default_factory=list, description="按综合得分降序的供应商评分")
    summary: str = Field(default="", description="一句话结论")

    def render(self) -> str:
        lines = [f"【{self.material_name}（{self.material_code}）比价】{self.summary}"]
        lines.extend("  " + s.render() for s in self.scores)
        return "\n".join(lines)
    