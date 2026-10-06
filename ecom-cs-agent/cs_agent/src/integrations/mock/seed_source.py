"""
mock 适配器的种子数据源。

职责只有一件事：把 `data/seed/*.json` 里**人写起来舒服**的结构，重组成
**上游系统真实返回**的形状。

为什么要在 mock 里多绕这一道
----------------------------
mock 的职责是模仿上游，不是模仿我们自己的标准结构。如果 mock 直接返回标准字段，
`integrations/normalize.py` 那一层在离线态就永远走不到 —— 它成了只为线上存在的
代码，demo 里没法验证，面试官也没法看。绕这一道之后，字段归一在离线态被
**真实地执行**，「orderNo → order_id」「19900 分 → 199.00 元」「40 → 400(已签收)」
这些轨迹是跑出来的，不是写死在输出里的。

三种上游形状的差异明细见 `integrations/normalize.py` 的模块说明。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from store.seed_loader import resolve_offset
from utils.agent_logger import get_logger

logger = get_logger("mock_seed_source")

# data/seed 相对本文件：src/integrations/mock/seed_source.py -> ../../../data/seed
# （词法 parents，不写 .resolve()：见 utils/env_loader.py 的说明）
_SEED_DIR = Path(__file__).parents[3] / "data" / "seed"


@lru_cache(maxsize=None)
def _read_json(filename: str) -> Dict[str, Any]:
    path = _SEED_DIR / filename
    if not path.exists():
        logger.warning(f"种子文件不存在：{path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # pragma: no cover
        logger.error(f"读取种子文件失败 {path}：{e}")
        return {}


def _epoch_ms(record: Dict[str, Any]) -> int:
    """相对偏移 -> 毫秒时间戳（上游 ERP 的 gmtCreate 就是这个格式）。"""
    moment = resolve_offset(record)
    return int(moment.timestamp() * 1000) if moment else 0


# ==================== 订单中台（ERP） ====================

def orders_raw() -> List[Dict[str, Any]]:
    """按订单中台的字段形状返回订单列表。

    与内部标准结构的差异（也就是归一层要处理的东西）：
      orderNo / buyerNick / tradeStatus(十位码) / gmtCreate(毫秒) /
      payFee、postFee、price(分) / receiverInfo(嵌套) / itemList
    """
    out: List[Dict[str, Any]] = []
    for order in _read_json("orders.json").get("orders", []):
        out.append({
            "orderNo": order.get("order_id", ""),
            "buyerNick": order.get("customer_name", ""),
            # 上游用十位码：400 -> 40
            "tradeStatus": int(order.get("status_code", 0)) // 10,
            "gmtCreate": _epoch_ms(order),
            "payFee": int(round(float(order.get("pay_amount", 0)) * 100)),
            "totalFee": int(round(float(order.get("total_amount", 0)) * 100)),
            "postFee": int(round(float(order.get("shipping_fee", 0)) * 100)),
            "warehouseName": order.get("warehouse", ""),
            "receiverInfo": order.get("receiver", {}),
            "itemList": [
                {
                    "skuCode": item.get("sku_id", ""),
                    "itemName": item.get("product_name", ""),
                    "num": item.get("quantity", 0),
                    # 行单价上游也给分
                    "price": int(round(float(item.get("unit_price", 0)) * 100)),
                }
                for item in order.get("items", [])
            ],
            # 内部附加字段：union_id 用来按客户查单。真实系统里这是另一个接口的入参，
            # 这里挂在报文上是为了让 mock 能支持 list_orders_by_customer。
            "_unionId": order.get("union_id", ""),
        })
    return out


# ==================== 快递查询（快递 100 风格） ====================

@lru_cache(maxsize=1)
def logistics_raw() -> Dict[str, Dict[str, Any]]:
    """按 `order_id` 索引的物流报文，形状模仿第三方快递查询接口：
    waybillNo / expressCompany / state(中文) / data[].{ftime, context}
    """
    out: Dict[str, Dict[str, Any]] = {}
    for trace in _read_json("logistics.json").get("traces", []):
        out[trace.get("order_id", "")] = {
            "waybillNo": trace.get("tracking_no", ""),
            "expressCompany": trace.get("carrier", ""),
            "state": trace.get("status", ""),
            "data": [
                {
                    "ftime": _node_time(node),
                    "context": node.get("desc", ""),
                    "status": node.get("status", ""),
                }
                for node in trace.get("nodes", [])
            ],
        }
    return out


def _node_time(node: Dict[str, Any]) -> str:
    moment = resolve_offset(node)
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else ""


# ==================== 仓储 WMS ====================

def inventory_raw() -> List[Dict[str, Any]]:
    """按 WMS 的字段形状返回库存：skuCode / warehouseName / qtyAvailable /
    qtyReserved / restockEta。"""
    return [
        {
            "skuCode": row.get("sku_id", ""),
            "warehouseName": row.get("warehouse", ""),
            "qtyAvailable": row.get("available", 0),
            "qtyReserved": row.get("reserved", 0),
            "restockEta": row.get("restock_eta", ""),
        }
        for row in _read_json("inventory.json").get("inventory", [])
    ]


def reset_cache() -> None:
    """清空缓存（测试里改了种子文件后调用）。"""
    _read_json.cache_clear()
    logistics_raw.cache_clear()
