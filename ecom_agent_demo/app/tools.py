"""
业务工具层:多路业务 API 编排 + 字段归一 + 幂等控制

设计要点:
- 编排订单、物流、库存多路 API,事实性数据一律取自接口,模型仅负责选接口与转述
- 多接口结果做字段归一与状态映射,收敛为统一契约对象
- 写操作以「订单号 + 操作类型」为唯一键做幂等控制,防止重复退款或补发

说明:demo 用内存字典模拟业务系统接口,函数签名与真实 API 客户端一致。
"""
import re
from typing import Optional

from app.models import AfterSaleDecision, OrderInfo

# ------------------------------------------------ 模拟业务系统(订单/物流/库存)
_ORDER_DB = {
    "SO20260618001": {
        "order_no": "SO20260618001",
        "item_title": "智能保温杯 500ml",
        "sku_code": "SKU-CUP-500",
        "total_amount": 199.0,
        "order_status": "已发货",
        "create_time": "2026-06-18 10:23",
    },
    "SO20260620007": {
        "order_no": "SO20260620007",
        "item_title": "儿童安全座椅(9个月-12岁)",
        "sku_code": "SKU-SEAT-01",
        "total_amount": 1280.0,
        "order_status": "已签收",
        "create_time": "2026-06-20 15:40",
    },
}
_LOGISTICS_DB = {
    "SO20260618001": {"logistics_status": "运输中,预计 6 月 22 日送达", "carrier": "顺丰速运"},
    "SO20260620007": {"logistics_status": "已签收(6 月 22 日 14:05 本人签收)", "carrier": "京东物流"},
}
_STOCK_DB = {
    "SKU-CUP-500": {"available": 320, "warehouse": "上海仓"},
    "SKU-SEAT-01": {"available": 46, "warehouse": "广州仓"},
}

# 字段归一映射:外部接口字段名 -> 统一契约字段名
FIELD_MAPPING = {
    "order_no": "order_id",
    "item_title": "product",
    "total_amount": "amount",
    "order_status": "status",
    "create_time": "created_at",
}

ORDER_ID_PATTERN = re.compile(r"[A-Z]{2}\d{8,}")


# ------------------------------------------------------------------ 订单 API
def extract_order_id(text: str) -> Optional[str]:
    """从自然语言中抽取订单号(业务侧确定性提取,不交给模型)"""
    m = ORDER_ID_PATTERN.search(text.upper())
    return m.group(0) if m else None


def get_order_api(order_id: str) -> Optional[dict]:
    return _ORDER_DB.get(order_id)


def get_logistics_api(order_id: str) -> dict:
    return _LOGISTICS_DB.get(order_id, {"logistics_status": "暂无物流记录", "carrier": ""})


def get_stock_api(sku_code: str) -> dict:
    return _STOCK_DB.get(sku_code, {"available": 0, "warehouse": "未知"})


def normalize_order(order_raw: dict, logistics_raw: dict, stock_raw: dict) -> OrderInfo:
    """多接口结果字段归一 + 状态映射,产出统一契约对象"""
    merged = {FIELD_MAPPING.get(k, k): v for k, v in order_raw.items()}
    merged["logistics"] = logistics_raw.get("logistics_status", "")
    return OrderInfo(**merged)


# --------------------------------------------------------------- 售后政策规则
# 政策结构化:命中条件 -> 处理结论(高风险操作需人工确认)
def match_after_sale_policy(order: OrderInfo, query: str) -> AfterSaleDecision:
    q = query
    # 高风险:高金额退款(阈值 500 元)
    if any(k in q for k in ["退款", "退钱"]) and order.amount >= 500:
        return AfterSaleDecision(
            order_id=order.order_id,
            action="refund",
            amount=order.amount,
            risk_level="high",
            need_human=True,
            reason=f"退款金额 {order.amount} 元 ≥ 500 元风控阈值,需主管审批",
        )
    if any(k in q for k in ["退款", "退钱"]):
        return AfterSaleDecision(
            order_id=order.order_id, action="refund", amount=order.amount,
            risk_level="low", reason="小额退款,政策内自动执行",
        )
    if "换货" in q or "换" in q:
        return AfterSaleDecision(
            order_id=order.order_id, action="exchange", amount=0.0,
            risk_level="low", reason="签收 15 天内质量问题,符合换货政策",
        )
    return AfterSaleDecision(
        order_id=order.order_id, action="reship", amount=0.0,
        risk_level="low", reason="少发/破损补发,免运费补寄",
    )


# ------------------------------------------------------------- 幂等执行器
class IdempotencyStore:
    """
    幂等控制:以「订单号 + 操作类型」为唯一键
    重复请求直接返回首次执行结果,防止重复退款 / 重复补发
    """

    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self.hits = 0

    def execute(self, order_id: str, action: str, amount: float = 0.0) -> dict:
        key = f"{order_id}:{action}"
        if key in self._records:
            self.hits += 1
            rec = dict(self._records[key])
            rec["idempotent_replay"] = True
            return rec
        result = {
            "order_id": order_id,
            "action": action,
            "amount": amount,
            "ticket": f"AF{abs(hash(key)) % 10 ** 8:08d}",
            "idempotent_replay": False,
            "message": {"refund": "退款已受理,1~3 个工作日原路退回",
                        "exchange": "换货工单已创建,快递员上门取件",
                        "reship": "补发单已生成,48 小时内发出"}.get(action, "已受理"),
        }
        self._records[key] = result
        return result


idempotency_store = IdempotencyStore()
