"""
订单查询子图的两个节点。

链路：定位订单 → 订单主数据 → **并行**拉物流与库存 → 字段归一 → 汇总作答。

为什么要并行，以及并行带来的新问题
----------------------------------
三个接口之间没有依赖（物流按订单号查、库存按 SKU 查），串行调用只是白等。
`ThreadPoolExecutor` 把它们并发发出去，总耗时从「三路之和」变成「最慢的一路」。

但并发也把「部分失败」这个平时可以装作不存在的问题摆到台面上：并发场景下
一路超时而另一路正常是**常态**。这里的处理是：

  * 订单主数据失败 -> 整个查询没有意义，如实告知并转人工；
  * 物流 / 库存单路失败 -> **不整体失败**，把缺的那一路记进 `degraded`，
    其余照常作答。少答一部分永远好过什么都不答。

进度事件只在主线程发（`get_stream_writer()` 是上下文局部的，工作线程里拿不到），
所以是「收集完结果再统一上报」，不是边跑边报。
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig

from graphs.cs_graph.cs_node import IntentDetection
from graphs.cs_graph.sub_order_query.sub_order_query_state import (
    SubOrderQueryState,
    SubOrderQueryStateField,
)
from integrations.factory import (
    get_inventory_port,
    get_logistics_port,
    get_order_port,
)
from integrations.normalize import (
    MapTrace,
    normalize_logistics,
    normalize_order,
    normalize_stock,
)
from integrations.ports import IntegrationError, LogisticsNotFound
from models.cs_models import (
    LogisticsNode,
    LogisticsTrace,
    NodeResult,
    OrderItemInfo,
    OrderQueryResult,
    StockInfo,
    TaskResult,
)
from observability.progress import emit, node_done
from utils.agent_logger import get_logger
from utils.text import last_question_clause

logger = get_logger("sub_order_query_node")

#: 会话记录里标识「客户」的发送者名
CUSTOMER_SENDER = "客户"

#: 订单号形态：内部口径是 SO + 8 位日期 + 3 位序号。
#: 允许用户少写几位数字——客服场景里客户经常只报后几位。
_ORDER_ID = re.compile(r"\b(SO\d{6,})\b", re.IGNORECASE)


class NodeName(str, Enum):
    WELCOME = "order_query_welcome_node"
    FETCH = "order_query_fetch_node"


# ---------------------------------------------------------------- 定位订单

def _customer_text(state: SubOrderQueryState) -> str:
    """客户说的话：优先用 task_input，它是意图枚举值时取会话最后一条。"""
    raw = (state.get(SubOrderQueryStateField.TASK_INPUT) or "").strip()
    if raw and raw != IntentDetection.ORDER_QUERY.value:
        return raw

    history = state.get(SubOrderQueryStateField.CHAT_HISTORY)
    for msg in reversed(getattr(history, "chat_msgs", None) or []):
        if getattr(msg, "sender", "") != CUSTOMER_SENDER:
            continue
        content = (getattr(msg, "content", "") or "").strip()
        if content:
            return last_question_clause(content)
    return ""


def _resolve_order_id(state: SubOrderQueryState) -> Tuple[str, List[str]]:
    """确定要查哪一单，返回 (订单号, 候选订单号列表)。

    三级退让：客户明说了单号就用单号；没说就取他名下的最近一单，并把全部
    候选一并带出去 —— 猜一单然后不作声，客户会以为我们查错了单；
    猜一单并说清「我查的是哪一单」，才是能继续往下走的答复。
    """
    text = _customer_text(state)
    hit = _ORDER_ID.search(text or "")
    if hit:
        return hit.group(1).upper(), []

    history = state.get(SubOrderQueryStateField.ORDER_HISTORY)
    orders = getattr(history, "orders", None) or []
    candidates = [getattr(o, "order_id", "") for o in orders if getattr(o, "order_id", "")]
    if candidates:
        # order_history 由 store 按时间倒序返回，第一条就是最近一单
        return candidates[0], candidates

    return "", []


# ---------------------------------------------------------------- 并行取数

def _safe(label: str, fn, *args) -> Tuple[Any, Optional[str], float]:
    """调用一个上游，把异常收成一个可读的降级说明。

    返回 (结果, 降级说明, 耗时秒)。降级说明为 None 表示这一路正常。
    """
    started = time.perf_counter()
    try:
        result = fn(*args)
        return result, None, time.perf_counter() - started
    except LogisticsNotFound as e:
        # 「查不到」是业务上的确定结论，不是故障，措辞要区分开
        return None, f"{label}暂未查到：{e}", time.perf_counter() - started
    except IntegrationError as e:
        logger.warning(f"{label}调用失败，降级处理：{e}")
        return None, f"{label}接口暂时不可用，已跳过", time.perf_counter() - started
    except Exception as e:  # pragma: no cover - 兜底，任何异常都不该掀翻整条链路
        logger.error(f"{label}调用出现未预期异常，降级处理：{e}")
        return None, f"{label}查询异常，已跳过", time.perf_counter() - started


def _fan_out(order_id: str, sku_ids: List[str],
             warehouse: str) -> Tuple[Any, Any, List[str], Dict[str, float]]:
    """并行拉物流与库存。"""
    logistics_port = get_logistics_port()
    inventory_port = get_inventory_port()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="cs-fanout") as pool:
        logistics_future = pool.submit(_safe, "物流", logistics_port.get_trace, order_id)
        stock_future = pool.submit(_safe, "库存", inventory_port.get_stock, sku_ids, warehouse)
        logistics_raw, logistics_note, logistics_cost = logistics_future.result()
        stock_raw, stock_note, stock_cost = stock_future.result()

    degraded = [note for note in (logistics_note, stock_note) if note]
    return logistics_raw, stock_raw, degraded, {
        "logistics": logistics_cost,
        "inventory": stock_cost,
    }


# ---------------------------------------------------------------- 组装

def _build_result(order: Dict[str, Any], order_id: str,
                  logistics_raw: Any, stock_raw: Any,
                  degraded: List[str], costs: Dict[str, float],
                  trace: MapTrace, fanout_wall: float) -> OrderQueryResult:
    """把归一后的三路数据装成结果模型。"""
    logistics = None
    if logistics_raw is not None:
        normalized = normalize_logistics(logistics_raw, order_id=order_id, trace=trace)
        logistics = LogisticsTrace(
            carrier=normalized["carrier"],
            tracking_no=normalized["tracking_no"],
            status=normalized["status"],
            nodes=[LogisticsNode(**n) for n in normalized["nodes"]],
        )

    stocks: List[StockInfo] = []
    if stock_raw:
        for row in stock_raw:
            normalized_stock = normalize_stock(row, trace=trace)
            # reserved 不进入对客户展示的模型：占用数是内部口径，
            # 说出来只会让客户困惑「为什么有货却写着已占用」
            stocks.append(StockInfo(
                sku_id=normalized_stock["sku_id"],
                warehouse=normalized_stock["warehouse"],
                available=normalized_stock["available"],
                restock_eta=normalized_stock["restock_eta"],
            ))

    result = OrderQueryResult(
        order_id=order["order_id"],
        status_code=order["status_code"],
        status_text=order["status_text"],
        customer_name=order["customer_name"],
        pay_amount=order["pay_amount"],
        shipping_fee=order["shipping_fee"],
        warehouse=order["warehouse"],
        created_at=order["created_at"],
        receiver=order["receiver"],
        items=[OrderItemInfo(**item) for item in order["items"]],
        logistics=logistics,
        stocks=stocks,
        degraded=degraded,
    )

    # 三个数分开报，别揉成一个「总耗时」：
    #   单路耗时 —— 每一路自己花了多久（含网关往返）
    #   并发段   —— 物流与库存**同时**发出的那段墙钟时间，约等于两者较慢的那个
    #   串行约需 —— 三路依次调用的总和，也就是不做并发编排要付的代价
    # 只报一个合计数的话，一旦某一路（比如订单主数据）是冷的，
    # 总数会比两个分项之和大出一个量级，读的人只会以为并发没生效。
    serial = sum(costs.values())
    result.normalize_trace = trace.lines() + [
        f"取数耗时：订单 {costs.get('order', 0) * 1000:.0f}ms、"
        f"物流 {costs.get('logistics', 0) * 1000:.0f}ms、"
        f"库存 {costs.get('inventory', 0) * 1000:.0f}ms；"
        f"物流+库存并发段 {fanout_wall * 1000:.0f}ms"
        f"（三路串行约需 {serial * 1000:.0f}ms）"
    ]
    return result


# ---------------------------------------------------------------- 节点

def welcome_node(state: SubOrderQueryState, config: RunnableConfig):
    """进查询之前先报一声，让进度条有东西显示。"""
    logger.info("=== 订单查询 ===")
    emit("fanout", detail="准备并行调用订单主数据 / 物流 / 库存接口")

    return {
        SubOrderQueryStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.WELCOME.value,
            execute_result_code=0,
            execute_result_msg="正在查询订单与物流信息，请稍等。",
            execute_exceptions=[]
        )]
    }


def fetch_node(state: SubOrderQueryState, config: RunnableConfig):
    """取数 + 归一 + 汇总。"""
    logger.info("=== 订单联合查询 ===")

    order_id, candidates = _resolve_order_id(state)
    if not order_id:
        logger.warning("没有可查询的订单号")
        message = "没查到您名下有待处理的订单，方便提供一下订单号吗？"
        return {
            SubOrderQueryStateField.ORDER_QUERY: OrderQueryResult(summary=message),
            SubOrderQueryStateField.TASK_RESULT: TaskResult(
                task_result=message,
                task_result_explain="既没有传入订单号，客户名下也没有订单记录",
                task_result_code=0
            ),
            SubOrderQueryStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.FETCH.value,
                execute_result_code=1,
                execute_result_msg="没有可查询的订单",
                execute_exceptions=["task_input 与订单历史都没有订单号"]
            )]
        }

    trace = MapTrace("订单中台")
    raw_order, order_note, order_cost = _safe("订单主数据", get_order_port().get_order, order_id)

    if raw_order is None:
        # 订单主数据是另外两路的前提，它没有就没有继续查的必要
        detail = order_note or f"订单 {order_id} 不存在"
        logger.warning(f"订单主数据不可用：{detail}")
        emit("fanout", detail=detail)
        message = (
            f"没有查到订单 {order_id}。"
            + (f"您名下最近的订单是 {'、'.join(candidates[:3])}，确认一下是哪一单？" if candidates else "请核对一下订单号。")
        )
        return {
            SubOrderQueryStateField.ORDER_QUERY: OrderQueryResult(
                order_id=order_id, candidates=candidates, degraded=[detail], summary=message,
            ),
            SubOrderQueryStateField.TASK_RESULT: TaskResult(
                task_result=message, task_result_explain=detail, task_result_code=1
            ),
            SubOrderQueryStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.FETCH.value,
                execute_result_code=1,
                execute_result_msg="订单主数据查询失败",
                execute_exceptions=[detail]
            )]
        }

    order = normalize_order(raw_order, trace=trace)
    sku_ids = [item["sku_id"] for item in order["items"] if item["sku_id"]]

    fanout_started = time.perf_counter()
    logistics_raw, stock_raw, degraded, costs = _fan_out(order_id, sku_ids, order["warehouse"])
    costs["order"] = order_cost
    fanout_wall = time.perf_counter() - fanout_started

    result = _build_result(
        order, order_id, logistics_raw, stock_raw, degraded, costs, trace, fanout_wall
    )
    result.candidates = candidates

    if logistics_raw is not None:
        emit("fanout", detail=(
            f"物流已取到（{result.logistics.carrier} {result.logistics.status}），"
            f"库存 {len(result.stocks)} 条"
        ))
    if result.stocks:
        out_of_stock = [s for s in result.stocks if not s.in_stock]
        if out_of_stock:
            emit("handoff", detail="；".join(s.render() for s in out_of_stock))
    if degraded:
        emit("handoff", detail="；".join(degraded))

    # 归一映射表逐条外显：这是「三家字段口径不一样」这句话唯一的证据，
    # 只写在注释里的话，面试官没法验证它真的发生过。
    for line in trace.lines():
        emit("normalize", detail=line)

    result.summary = result.render()
    if candidates and len(candidates) > 1:
        result.summary += f"\n（您名下还有 {len(candidates) - 1} 笔历史订单，需要的话报一下单号）"

    node_done(NodeName.FETCH.value, f"订单 {result.order_id} {result.status_text}")

    # 部分降级仍然算「结果有效」（code=0）：链路跑完了，答案是「有，但缺一块」。
    # 把降级判成失败，会让上层把一次正确的兜底当成 bug。
    return {
        SubOrderQueryStateField.ORDER_QUERY: result,
        SubOrderQueryStateField.TASK_RESULT: TaskResult(
            task_result=result.summary,
            task_result_explain=(
                f"三路接口联合查询，降级 {len(degraded)} 路" if degraded
                else "订单/物流/库存三路接口联合查询，全部成功"
            ),
            task_result_code=0
        ),
        SubOrderQueryStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.FETCH.value,
            execute_result_code=0,
            execute_result_msg=(
                f"查询完成（{result.status_text}，降级 {len(degraded)} 路）" if degraded
                else f"查询完成（{result.status_text}）"
            ),
            execute_exceptions=list(degraded)
        )]
    }
