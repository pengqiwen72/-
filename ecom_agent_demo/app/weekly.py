"""
数据周报层:多平台异构数据整合 + 代码计算 + 模型摘要

设计要点:字段映射、数据清洗与同环比计算全部由代码完成,模型只把统计结果写成文字摘要
—— 数值计算交给代码、语义总结交给模型,避免大模型参与计算导致数据失真

数值全部由代码计算;LLM 只负责把统计结果写成一段话。
"""
from typing import List

from app.models import WeeklyStats

# 多平台异构原始数据(demo):字段名不统一、含缺失值,需要映射与清洗
PLATFORM_DATA = {
    "主站": [
        {"dt": "2026-06-08/2026-06-14", "gmv": 128000, "ord_cnt": 860, "refund_cnt": 34, "refund_amt": 8600},
        {"dt": "2026-06-15/2026-06-21", "gmv": 152000, "ord_cnt": 1010, "refund_cnt": 38, "refund_amt": 9600},
    ],
    "小程序": [
        {"date_range": "2026-06-08~2026-06-14", "sale_amount": 46000, "order_num": 310, "refund_num": 21},
        {"date_range": "2026-06-15~2026-06-21", "sale_amount": 51500, "order_num": None, "refund_num": 19},
    ],
}

# 字段映射:各平台字段名 -> 统一字段
PLATFORM_FIELD_MAPPING = {
    "dt": "period", "date_range": "period",
    "gmv": "gmv", "sale_amount": "gmv",
    "ord_cnt": "orders", "order_num": "orders",
    "refund_cnt": "refunds", "refund_num": "refunds",
    "refund_amt": "refund_amount",
}


def _clean(records: List[dict]) -> List[dict]:
    """清洗:统一字段名 + 类型转换 + 缺失值处理(计数类用上一周期同平台值前值填充)"""
    cleaned: List[dict] = []
    prev: dict = {}
    for rec in records:
        item = {PLATFORM_FIELD_MAPPING.get(k, k): v for k, v in rec.items() if k in PLATFORM_FIELD_MAPPING}

        def _fill(field: str, default: float) -> float:
            """缺失值:优先取本周期值,缺失则沿用上一周期值,再缺失则取默认值"""
            value = item.get(field)
            return value if value is not None else prev.get(field, default)

        item["gmv"] = float(_fill("gmv", 0))
        item["orders"] = int(_fill("orders", 0))
        item["refunds"] = int(_fill("refunds", 0))
        cleaned.append(item)
        prev = item
    return cleaned


def _aggregate(period_index: int) -> dict:
    """跨平台按周期汇总"""
    gmv = orders = refunds = 0
    for platform_records in PLATFORM_DATA.values():
        rec = _clean(platform_records)[period_index]
        gmv += rec["gmv"]
        orders += rec["orders"]
        refunds += rec["refunds"]
    return {"gmv": gmv, "orders": orders, "refunds": refunds}


def _wow(current: float, previous: float) -> float:
    """环比计算(代码计算,模型不参与)"""
    if not previous:
        return 0.0
    return (current - previous) / previous * 100


def build_weekly_stats() -> WeeklyStats:
    """生成周报统计数据:字段映射 -> 清洗 -> 跨平台汇总 -> 同环比"""
    cur = _aggregate(1)   # 本周(6/15-6/21)
    prev = _aggregate(0)  # 上周(6/8-6/14)

    refund_rate_cur = cur["refunds"] / cur["orders"] if cur["orders"] else 0.0
    refund_rate_prev = prev["refunds"] / prev["orders"] if prev["orders"] else 0.0

    # 异常提示(规则判定,不依赖模型)
    abnormal = []
    if cur["refunds"] > prev["refunds"]:
        abnormal.append("退款单量环比上升,需排查商品质量与描述一致性问题")
    if cur["gmv"] and _wow(cur["gmv"], prev["gmv"]) < 0:
        abnormal.append("GMV 环比下滑,建议核查流量与转化")

    return WeeklyStats(
        week="2026-06-15 ~ 2026-06-21",
        gmv=float(cur["gmv"]),
        gmv_wow=round(_wow(cur["gmv"], prev["gmv"]), 1),
        orders=cur["orders"],
        orders_wow=round(_wow(cur["orders"], prev["orders"]), 1),
        refund_rate=round(refund_rate_cur, 4),
        refund_rate_wow=round((refund_rate_cur - refund_rate_prev) * 100, 1),
        abnormal=abnormal,
    )
