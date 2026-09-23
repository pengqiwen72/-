"""
把 `data/seed/*.json` 灌进 LangGraph Store。

为什么需要它
------------
图里的节点全部通过 store 读业务数据（员工、客户、标签、会话、订单）。离线跑
demo 时 store 是空的，于是每个节点都返回空值 —— 「能跑通」但看不到任何业务
产出。这个 loader 把一份**虚构但自洽**的电商数据集灌进去，让全链路有真实
内容可处理。

时间为什么用相对偏移
--------------------
种子数据里写的是 `days_ago: 9` / `hours_ago: 5` 而不是 `2026-03-15 15:40`。
如果写死绝对时间，周报里的「本周 / 上周」在你 clone 仓库一个月后就全空了，
同环比会变成 0，demo 直接失去意义。改成加载时按当前时间回推，
保证「最近一周有订单、最近几小时有对话」在任何时候都成立。

用法
----
    from store.seed_loader import load_seed_data
    load_seed_data(store)           # 幂等，重复调用只是覆盖同样的键
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.store.base import BaseStore

from utils.agent_logger import get_logger

logger = get_logger("seed_loader")

# data/seed 相对本文件：src/store/seed_loader.py -> ../../data/seed
_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

# 东八区偏移（考虑夏令时不需要，中国不使用夏令时）
_TZ_CHINA = timezone(timedelta(hours=8))

# 时间偏移字段 -> 对应的 timedelta 参数名
_OFFSET_UNITS = {
    "minutes_ago": "minutes",
    "hours_ago": "hours",
    "days_ago": "days",
}


# ==================== 工具 ====================

def _read_json(filename: str) -> Dict[str, Any]:
    path = _SEED_DIR / filename
    if not path.exists():
        logger.warning(f"种子文件不存在，跳过：{path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取种子文件失败 {path}: {e}")
        return {}


def resolve_offset(record: Dict[str, Any]) -> Optional[datetime]:
    """把记录里的相对时间描述解成一个具体时刻（东八区）。

    支持 `minutes_ago` / `hours_ago` / `days_ago`（可带小数），
    以及可选的 `at`（"HH:MM" 或 "HH:MM:SS"）用来把时刻钉在某个整点附近。

    返回 None 表示这条记录没有时间信息（有些数据本来就不需要）。
    """
    now = datetime.now(_TZ_CHINA)

    delta = timedelta(0)
    found = False
    for field, unit in _OFFSET_UNITS.items():
        value = record.get(field)
        if value is None:
            continue
        delta += timedelta(**{unit: float(value)})
        found = True

    if not found:
        return None

    moment = now - delta

    # `at` 把时刻钉到当天的指定时分，避免「9 天前」落在当前这一刻导致
    # 同一批数据的先后顺序随运行时间漂移
    at = record.get("at")
    if at:
        try:
            parts = [int(p) for p in str(at).split(":")]
            while len(parts) < 3:
                parts.append(0)
            moment = moment.replace(hour=parts[0], minute=parts[1],
                                    second=parts[2], microsecond=0)
        except (ValueError, TypeError):
            logger.warning(f"无法解析时间点 at={at!r}，忽略")

    return moment


def to_timestamp(record: Dict[str, Any]) -> int:
    """转成 UTC 秒级时间戳（store 里存的格式，下游用 timestamp2datetime 还原）。"""
    moment = resolve_offset(record)
    return int(moment.timestamp()) if moment else 0


def to_yyyymmdd(record: Dict[str, Any]) -> str:
    """转成 YYYYMMDD，供 store 的区间过滤使用。"""
    moment = resolve_offset(record)
    return moment.strftime("%Y%m%d") if moment else ""


# ==================== 各类数据的写入 ====================

def _load_employees(store: BaseStore) -> int:
    data = _read_json("employees.json")
    count = 0
    for emp in data.get("employees", []):
        user_id = emp.get("user_id")
        if not user_id:
            continue
        store.put(("employee",), user_id, emp)
        count += 1
    return count


def _load_tags(store: BaseStore) -> int:
    data = _read_json("tags.json")
    count = 0
    for tag in data.get("tags", []):
        tag_id = tag.get("tag_id")
        if not tag_id:
            continue
        # deleted 必须显式写：list_all_tags_setting 的过滤条件是 {"deleted": False}
        tag.setdefault("deleted", False)
        store.put(("tags_setting",), tag_id, tag)
        count += 1
    return count


def _load_customers(store: BaseStore) -> int:
    data = _read_json("customers.json")
    count = 0
    for customer in data.get("customers", []):
        external_id = customer.get("external_id")
        follow_user_id = customer.get("follow_user_id")
        if not external_id or not follow_user_id:
            continue

        # 基础信息：profile 单独放另一个命名空间，这里剔除掉
        base = {k: v for k, v in customer.items() if k != "profile"}
        base.setdefault("tags", [])
        store.put(("external_user", follow_user_id), external_id, base)

        # 画像
        profile = customer.get("profile") or {}
        if profile:
            store.put(("external_user_profile", follow_user_id), external_id, profile)

        count += 1
    return count


def _load_chats(store: BaseStore) -> int:
    data = _read_json("chats.json")
    count = 0

    # 企业微信私聊
    for i, msg in enumerate(data.get("wxqy_msgs", [])):
        external_id = msg.get("external_id")
        follow_user_id = msg.get("follow_user_id")
        if not external_id or not follow_user_id:
            continue

        # ⚠️ 命名空间键必须是两个 id 排序后拼接 —— store_client 就是这么算的，
        #    这里不一致的话消息写进去也读不出来。
        namespace = ("wxqy_msg", "".join(sorted([external_id, follow_user_id])))
        record = {
            "from_id": msg.get("from_id", ""),
            "content": msg.get("content", ""),
            "msg_time": to_timestamp(msg),
            "YYYYMMDD": to_yyyymmdd(msg),
        }
        store.put(namespace, f"msg-{i:04d}", record)
        count += 1

    # 微信客服会话
    for i, msg in enumerate(data.get("wxkf_msgs", [])):
        external_id = msg.get("external_id")
        if not external_id:
            continue

        record = {
            "external_id": external_id,
            "content": msg.get("content", ""),
            "origin": msg.get("origin", 3),
            "msg_time": to_timestamp(msg),
            "YYYYMMDD": to_yyyymmdd(msg),
        }
        store.put(("wxkf_msg", external_id), f"kf-{i:04d}", record)
        count += 1

    return count


def _load_orders(store: BaseStore) -> int:
    """把订单写进 `wxxd_order` 命名空间（图里的订单历史走这里）。"""
    data = _read_json("orders.json")
    count = 0
    for i, order in enumerate(data.get("orders", [])):
        union_id = order.get("union_id")
        order_id = order.get("order_id")
        if not union_id or not order_id:
            continue

        record = {
            "order_id": order_id,
            "order_products": [
                item.get("product_name", "") for item in order.get("items", [])
            ],
            "order_amount": order.get("total_amount", 0.0),
            "order_status": order.get("status_text", ""),
            "order_create_time": to_timestamp(order),
            "YYYYMMDD": to_yyyymmdd(order),
        }
        store.put(("wxxd_order", union_id), order_id, record)
        count += 1
    return count


# ==================== 对外入口 ====================

def load_seed_data(store: Optional[BaseStore] = None) -> Dict[str, int]:
    """把全部种子数据灌进 store，返回各类数据的写入条数。

    幂等：同一份种子重复加载只是覆盖同样的键，不会产生重复记录。
    """
    if store is None:
        from store.store_provider import get_fallback_store

        store = get_fallback_store()

    stats = {
        "employee": _load_employees(store),
        "tags_setting": _load_tags(store),
        "external_user": _load_customers(store),
        "chat_msg": _load_chats(store),
        "order": _load_orders(store),
    }

    total = sum(stats.values())
    logger.info(
        "种子数据加载完成："
        + "，".join(f"{k}={v}" for k, v in stats.items())
        + f"，共 {total} 条"
    )
    return stats


if __name__ == "__main__":  # pragma: no cover - 手动排查用
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    print(load_seed_data())
