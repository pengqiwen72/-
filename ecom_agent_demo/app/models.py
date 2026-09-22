"""
数据模型层:Pydantic 结构化模型

校验策略:模型输出经结构化校验后进入流程,格式异常时兜底为空模型,避免脏数据流入 State
- LLM 返回的 JSON -> LLMOutput.model_validate_json(...) 校验
- 校验失败 -> 空模型兜底,脏数据不流入 State
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError


class Intent(str, Enum):
    """业务意图枚举(高频意图走精确匹配,不消耗模型调用)"""

    PRODUCT_QA = "product_qa"        # 商品咨询
    ORDER_QUERY = "order_query"      # 订单/物流查询
    AFTER_SALE = "after_sale"        # 售后处理
    WEEKLY_REPORT = "weekly_report"  # 数据周报
    GENERAL = "general"              # 兜底通用问答


class SourceDoc(BaseModel):
    """知识出处(回答强制携带,可溯源)"""

    doc_name: str
    content: str
    score: float = Field(ge=0.0, le=1.0)


class LLMOutput(BaseModel):
    """模型输出的结构化契约"""

    answer: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @classmethod
    def safe_parse(cls, raw: str) -> "LLMOutput":
        """安全解析:校验失败返回空模型兜底,避免脏数据流入 State"""
        try:
            return cls.model_validate_json(raw)
        except (ValidationError, ValueError):
            return cls()


class OrderInfo(BaseModel):
    """订单信息(事实性数据:必须来自业务 API,模型只转述)"""

    order_id: str
    product: str
    amount: float
    status: str
    created_at: str
    logistics: str = ""


class AfterSaleDecision(BaseModel):
    """售后处理结论(由政策规则匹配生成)"""

    order_id: str
    action: str                    # refund / exchange / reship
    amount: float
    risk_level: str                # low / high
    need_human: bool = False
    reason: str = ""


class TaskResult(BaseModel):
    """单次任务的统一输出"""

    intent: str
    status: str                    # success / need_human / fallback
    content: str
    sources: List[SourceDoc] = Field(default_factory=list)
    latency_ms: int = 0


class NodeResult(BaseModel):
    """节点执行记录(自定义 reducer 增量合并的载体)"""

    node: str
    detail: str


class WeeklyStats(BaseModel):
    """周报统计(数值全部由代码计算,模型只写摘要)"""

    week: str
    gmv: float
    gmv_wow: float                 # 环比
    orders: int
    orders_wow: float
    refund_rate: float
    refund_rate_wow: float
    abnormal: List[str] = Field(default_factory=list)
