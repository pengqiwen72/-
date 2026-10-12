"""
供应链采购仓储域的领域模型。

组织方式：通用件在前（兜底应答 / 意图 / 任务结果），业务件在后（主数据 →
要素提取 → 执行方案 → 查询结果），与主图「装载 → 路由 → 子图」的执行顺序一致。
`data/seed/*.json` 与本文件一一对应，字段口径改动要两边一起改 ——
`store/seed_loader.py::verify_seed_data` 会按这里的字段做校验。
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from typing_extensions import Annotated
from models.scm_base_model import ScmBaseModel

# 使用 Annotated 类型约束
TimeStr = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')]

#####  讨论  #####

class JustTalkOutput(ScmBaseModel):
    ''' 讨论应答 '''
    just_talk_output: str = Field(default="", description="讨论应答")
    
    @classmethod
    def get_example_instance(cls):
        return cls(
            just_talk_output="输出应答内容"
        )


#####  意图检测  #####
class Intent(ScmBaseModel):
    ''' 意图 '''
    intent_id: str = Field(default="", description="意图ID")
    intent_description: str = Field(default="", description="意图描述")

    @classmethod
    def get_example_instance(cls):
        return cls(
            intent_id="material_qa",
            intent_description="查询物料规格 / 替代料，或采购制度、验收标准类问题"
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


class OperatorInfo(ScmBaseModel):
    ''' 业务操作员（提报 / 审批 / 质检 / 库管） '''
    operator_id: str = Field(default="", description="操作员编码，形如 OP001")
    name: str = Field(default="", description="姓名")
    role: str = Field(default="", description="角色：采购员 / 采购主管 / 质检员 / 仓库管理员")
    department: str = Field(default="", description="所属部门")

    @classmethod
    def get_example_instance(cls):
        return cls(operator_id="OP001", name="张伟", role="采购员", department="采购部")


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


#####  采购订单记录（已下达的单，收货链路挂载点）  #####

class PurchaseOrderInfo(ScmBaseModel):
    ''' 采购订单（已下达，等待发货 / 在途 / 已入库）

    与 `PurchasePlan` 的区别：Plan 是「这次申请的处理方案」（还没下单），
    Order 是「已经和供应商敲定的单」（收货、对账、异常工单都挂在它身上）。
    收货子图靠 po_no 在它身上找物料、数量与到货仓库，不再依赖用户复述。
    '''

    po_no: str = Field(default="", description="采购订单号，形如 PO20260928001")
    request_id: str = Field(default="", description="来源采购申请号（PR 开头）")
    material_code: str = Field(default="", description="物料编码")
    qty: float = Field(default=0.0, description="订购数量")
    unit: str = Field(default="", description="数量单位")
    unit_price: float = Field(default=0.0, description="单价（元）")
    amount: float = Field(default=0.0, description="订单金额（元）")
    supplier_code: str = Field(default="", description="供应商编码")
    target_warehouse: str = Field(default="", description="到货仓库编码")
    status: str = Field(default="", description="状态机器码：created / in_transit / received")
    status_text: str = Field(default="", description="状态中文文案")
    operator_id: str = Field(default="", description="经办操作员")

    @classmethod
    def get_example_instance(cls):
        return cls(
            po_no="PO20260928002", request_id="PR20260926007366",
            material_code="M400301", qty=1000, unit="支", unit_price=12.50,
            amount=12500.00, supplier_code="S004", target_warehouse="WH02",
            status="in_transit", status_text="在途", operator_id="OP001",
        )

    def render(self) -> str:
        return (
            f"{self.po_no}：{self.material_code} {self.qty:g}{self.unit}"
            f" @ {self.unit_price:g} 元 → {self.target_warehouse}"
            f"（{self.status_text or self.status}）"
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
        description="matched / pending_approval / approved / rejected / executed / replayed / conflict / failed",
    )
    revision: int = Field(
        default=0,
        description="审批轮次：recreate 按修改重报一次加一轮；审批记录按轮次各记一笔",
    )
    approved_by: str = Field(default="", description="审批人")
    replayed: bool = Field(default=False, description="是否命中了幂等（本次没有真的再下过单）")
    operator_id: str = Field(default="", description="提报人")
    description: str = Field(default="", description="申请说明")
    message: str = Field(default="", description="一句话结论")
    po_no: str = Field(default="", description="下达得到的采购订单号；未执行时为空")

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
        if self.po_no:
            lines.append(f"采购订单：{self.po_no}")
        return "\n".join(p for p in lines if p)


class ReceiptPlan(ScmBaseModel):
    ''' 收货质检方案（策略结果 + 复核状态，字段与中断 payload 一一对应）

    `defect_rate` 由 `compute_defect_rate` 统一计算：口径只有一处，
    策略引擎的阈值判定与展示文案读的是同一个值。

    `biz_id` 是登记号（由要素确定性生成，同输入必同号，幂等锚点）；
    `receipt_no` 是 WMS 执行成功后回填的正式单号 —— 展示一律
    「有正式用正式」，否则同一批货会冒出两个都叫「收货单号」的数字。
    '''

    plan_id: str = Field(default="", description="方案 ID，由 biz_id 确定性生成（uuid5）")
    biz_id: str = Field(default="", description="业务单号：RC 开头的收货登记号，由要素确定性生成")
    receipt_no: str = Field(default="", description="WMS 正式收货单号，执行成功后回填")
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
        description="matched / pending_review / approved / rejected / executed / replayed / conflict / failed",
    )
    revision: int = Field(
        default=0,
        description="复核轮次：recreate 按修改重报一次加一轮；复核记录按轮次各记一笔",
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
        number = self.receipt_no or self.biz_id
        if number:
            lines.append(f"收货单号：{number}")
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


class SupplierPriceLine(ScmBaseModel):
    ''' 供应商在单个物料上的报价竞争力（履约评估的明细行） '''
    material_code: str = Field(default="", description="物料编码")
    material_name: str = Field(default="", description="物料名称")
    unit: str = Field(default="", description="计量单位")
    unit_price: float = Field(default=0.0, description="本家报价（元）")
    is_lowest: bool = Field(default=False, description="是否该物料的最低报价")
    price_score: float = Field(default=0.0, description="价格分 = 该物料最低价 / 本家报价")

    def render(self) -> str:
        tag = "（最低报价）" if self.is_lowest else ""
        return (
            f"{self.material_code}（{self.material_name}）：报价 "
            f"{self.unit_price:.2f} 元/{self.unit}，价格分 {self.price_score:.3f}{tag}"
        )


class SupplierEvaluation(ScmBaseModel):
    ''' 供应商履约评估：基础履约指标 + 跨物料价格竞争力 + 综合评分

    评分与 `SupplierComparison` 同一套加权口径（0.5/0.3/0.2），差别只在
    价格分是**跨其报价的各物料**取平均——单家供应商没有「一个物料」当基准，
    聚合后的平均价格分与比价场景里的价格分语义一致。
    '''
    supplier_code: str = Field(default="", description="供应商编码")
    supplier_name: str = Field(default="", description="供应商名称")
    is_new: bool = Field(default=False, description="是否新供应商（首单需审批）")
    cooperation_since: str = Field(default="", description="合作起始（新供应商为空）")
    lead_time_days: int = Field(default=0, description="承诺交期（天）")
    on_time_rate: float = Field(default=0.0, description="历史准时交付率，[0,1]")
    defect_rate: float = Field(default=0.0, description="历史到货不良率，[0,1]")
    quoted_count: int = Field(default=0, description="有报价的物料数")
    lowest_count: int = Field(default=0, description="其中报价最低的物料数")
    avg_price_score: float = Field(default=0.0, description="跨物料的平均价格分")
    score: float = Field(default=0.0, description="综合履约评分，[0,1]")
    lines: List[SupplierPriceLine] = Field(default_factory=list, description="各物料报价竞争力")
    note: str = Field(default="", description="降级或口径说明")
    summary: str = Field(default="", description="一句话结论")

    def render(self) -> str:
        metrics = (
            f"承诺交期 {self.lead_time_days} 天，准时交付率 {self.on_time_rate:.0%}，"
            f"到货不良率 {self.defect_rate:.2%}"
        )
        lines = [f"【供应商履约评估：{self.supplier_name}（{self.supplier_code}）】{self.summary}"]
        # 降级时 summary 就是这句基础指标（没有价格竞争力可概括），别再重复一行
        if metrics not in self.summary:
            lines.append(f"  {metrics}")
        if self.lines:
            lines.append(
                f"  价格竞争力：{self.quoted_count} 个在报物料，其中 "
                f"{self.lowest_count} 个为最低报价，平均价格分 {self.avg_price_score:.3f}"
            )
            lines.append(
                f"  综合履约评分 {self.score:.3f}"
                f"（0.5×价格分 + 0.3×准时率 + 0.2×质量）"
            )
            lines.extend("  " + line.render() for line in self.lines)
        if self.note:
            lines.append(f"  {self.note}")
        return "\n".join(lines)
    
