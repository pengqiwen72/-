"""
守的是「多系统字段归一 + 部分失败降级」这条线
（`src/integrations/normalize.py` + `sub_order_query` 子图）。

订单链路要同时打三个业务系统，三家的报文形状各不相同，归一层负责四件事：
命名口径（orderNo → order_id）、枚举口径（30 → 300 已发货）、
单位量纲（分 → 元、毫秒时间戳 → 日期串）、结构层级（嵌套地址 → 一行文本）。
这些映射在离线态是**真的被执行的**：mock 适配器返回的就是上游形状。

降级语义同样要有牙：
  * 物流或库存单路失败 → 不整体失败，缺的那一路记进 degraded、如实告知客户；
  * 订单主数据失败 → 整个查询没有意义，这次才算真失败；
  * 「查不到轨迹」是业务上的确定结论，与「接口挂了」是两回事，措辞必须能分开。

断言断在语义上（映射的方向、单位的换算关系、升序、谁降级了），不逐字抄输出。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from integrations.factory import get_inventory_port, get_logistics_port, get_order_port
from integrations.normalize import (
    MapTrace,
    flatten_address,
    format_time,
    normalize_logistics,
    normalize_order,
    normalize_status,
    normalize_stock,
)

_TZ_CHINA = timezone(timedelta(hours=8))

_ORDER_NO = "SO20260318002"          # 种子：机械键盘，已发货，实付 459 元
_ORDER_NO_NO_TRACE = "SO20260311004"  # 种子：已签收，但没有任何物流轨迹
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ==================== 一、字段归一（直接对归一层） ====================

def test_order_normalization_maps_alias_enum_and_cent_units():
    """订单中台报文：别名、十位状态码、以分为单位的金额，一次全对。"""
    raw = get_order_port().get_order(_ORDER_NO)
    assert raw, "mock 应当返回上游形状的订单报文"

    # 先确认拿到的确实是**上游形状**：标准字段名一个都不在
    assert "orderNo" in raw and "order_id" not in raw
    assert "payFee" in raw and "pay_amount" not in raw

    trace = MapTrace("订单中台")
    norm = normalize_order(raw, trace=trace)

    assert norm["order_id"] == raw["orderNo"]
    assert norm["customer_name"] == raw["buyerNick"]
    # 枚举口径：上游十位码 30 -> 内部 300（已发货）
    assert norm["status_code"] == str(int(raw["tradeStatus"]) * 10)
    assert norm["status_text"] == "已发货"
    # 单位换算：分 -> 元。比例关系比某个具体数字更值得断言
    assert norm["pay_amount"] == pytest.approx(raw["payFee"] / 100.0)
    assert norm["shipping_fee"] == pytest.approx(raw["postFee"] / 100.0)

    item, raw_item = norm["items"][0], raw["itemList"][0]
    assert item["sku_id"] == raw_item["skuCode"]
    assert item["quantity"] == raw_item["num"]
    assert item["unit_price"] == pytest.approx(raw_item["price"] / 100.0)

    # 归一轨迹要能指出「哪一家的哪个字段被映射了」——这是可解释性的全部证据
    lines = trace.lines()
    assert any("orderNo" in ln and "order_id" in ln for ln in lines)
    assert any("分" in ln for ln in lines)


def test_order_created_at_is_a_readable_datetime():
    """毫秒时间戳 -> `yyyy-MM-dd HH:mm:ss`，下游拿到的是能直接展示的字符串。"""
    raw = get_order_port().get_order(_ORDER_NO)
    norm = normalize_order(raw)
    assert _TIME_RE.match(norm["created_at"]), norm["created_at"]
    assert re.match(r"^\d{4}-\d{2}-\d{2} ", norm["created_at"])


@pytest.mark.parametrize("field", ["payFee", "price"])
def test_amounts_are_分_only_for_cent_fields(field):
    """单位属于**来源字段名**，不属于数值：同一个 199 在两个字段名下含义不同。

    `payFee`（分）与 `payAmount`（元）在数值上不可区分 —— 靠大小猜迟早把
    199 元读成 1.99 元。这条用例把「按字段名判单位」钉住。
    """
    from integrations.normalize import to_yuan

    cents = to_yuan(19900, source_field="payFee", canonical="pay_amount")
    yuan = to_yuan(199, source_field="payAmount", canonical="pay_amount")
    assert cents == pytest.approx(199.0)
    assert yuan == pytest.approx(199.0)
    assert to_yuan(19900, source_field="payFee", canonical="x") != 19900


def test_receiver_is_flattened_and_phone_is_masked():
    """嵌套地址拍平成一行，顺带把手机号脱敏 —— 明文号码不该进日志与页面。"""
    receiver = {
        "name": "李女士",
        "phone": "13812346721",
        "province": "浙江省",
        "city": "杭州市",
        "district": "西湖区",
        "detail": "文三路 8 号 A 座 1203 室",
    }
    text = flatten_address(receiver, source_field="receiverInfo")
    assert "李女士" in text
    assert "浙江省杭州市西湖区文三路 8 号" in text
    assert "13812346721" not in text
    assert re.search(r"1[3-9]\d\*{4}\d{4}", text)


def test_status_normalization_keeps_unknown_values_instead_of_guessing():
    """认不出来的状态：保留原文，而不是硬猜一个错的码给客户看。"""
    code, text = normalize_status(9999)
    assert code == "" and text == "9999"

    code, text = normalize_status("待揽收")
    assert code == "" and text == "待揽收"

    # 中文文案与内部码两个方向都要认
    assert normalize_status("已签收")[0] == "400"
    assert normalize_status(400)[0] == "400"


def test_timestamp_seconds_and_milliseconds_both_normalize_to_the_same_moment():
    """秒级与毫秒级时间戳都要认，且落在东八区的同一自然时刻。"""
    moment = datetime(2026, 3, 16, 10, 0, 0, tzinfo=_TZ_CHINA)
    seconds = int(moment.timestamp())
    expected = moment.strftime("%Y-%m-%d %H:%M:%S")

    assert format_time(seconds * 1000, source_field="gmtCreate") == expected
    assert format_time(seconds, source_field="createTime") == expected
    # 字符串形态（含 ISO 带 Z）同样收敛到同一格式
    assert format_time("2026-03-16T10:00:00Z") == expected


def test_logistics_nodes_are_sorted_ascending():
    """轨迹节点按时间正序排好 —— 下游不用各自再排一遍，也不该拿到倒序。"""
    raw = get_logistics_port().get_trace(_ORDER_NO)
    norm = normalize_logistics(raw, order_id=_ORDER_NO)

    assert norm["carrier"] == raw["expressCompany"]
    assert norm["tracking_no"] == raw["waybillNo"]
    assert norm["status"] == raw["state"]

    times = [node["time"] for node in norm["nodes"]]
    assert times == sorted(times)
    assert all(_TIME_RE.match(t) for t in times)
    assert norm["nodes"][-1]["desc"] == raw["data"][-1]["context"]


def test_stock_normalization_maps_wms_fields():
    """仓储 WMS 的字段名与内部标准字段完全不同，必须走别名表。"""
    rows = get_inventory_port().get_stock(["SKU-KB80"], "华南仓")
    assert rows, "种子库存里应当有 SKU-KB80@华南仓"

    norm = normalize_stock(rows[0])
    assert norm["sku_id"] == rows[0]["skuCode"]
    assert norm["warehouse"] == rows[0]["warehouseName"]
    assert norm["available"] == rows[0]["qtyAvailable"]
    assert norm["reserved"] == rows[0]["qtyReserved"]


# ==================== 二、整链路：三路联合查询 ====================

def test_order_query_joins_three_systems_into_one_answer(runner):
    """实付金额、物流单号、库存数量三个来源的数据必须汇进同一条答复。"""
    result = runner.start(f"帮我查一下订单 {_ORDER_NO} 到哪了")
    assert result.error is None

    oq = result.state.get("order_query")
    assert oq is not None
    assert oq.order_id == _ORDER_NO
    assert oq.status_code == "300" and oq.status_text == "已发货"
    assert oq.pay_amount == pytest.approx(459.00)      # 上游给的是 45900 分
    assert _TIME_RE.match(oq.created_at)
    assert oq.receiver and "****" in oq.receiver       # 已扁平化 + 已脱敏

    # 物流：来自快递接口（waybillNo → tracking_no）
    assert oq.logistics is not None
    assert oq.logistics.carrier == "顺丰速运"
    assert oq.logistics.tracking_no.startswith("SF")
    node_times = [n.time for n in oq.logistics.nodes]
    assert node_times == sorted(node_times)

    # 库存：来自 WMS（skuCode → sku_id）
    assert any(s.sku_id == "SKU-KB80" and s.available > 0 and s.in_stock for s in oq.stocks)

    assert oq.degraded == []
    assert result.task_result.task_result_code == 0

    # 客户看到的那句话里，三路数据都在
    message = result.task_result.task_result
    assert oq.logistics.tracking_no in message
    assert f"{oq.pay_amount:.2f}" in message

    # 归一轨迹逐条外显（工作台时间线上的 normalize 行就来自这里）
    assert oq.normalize_trace
    assert any("orderNo" in ln for ln in oq.normalize_trace)


def test_partial_failure_degrades_that_leg_only(runner, monkeypatch):
    """物流这一路挂了：订单与库存照常返回，缺的一路进 degraded —— 整体仍是成功。

    「少答一部分永远好过什么都不答」；把降级判成失败会让上层把一次正确的
    兜底当成 bug，所以 task_result_code 必须仍是 0。
    """
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "logistics")
    result = runner.start(f"帮我查一下订单 {_ORDER_NO} 到哪了")

    assert result.error is None
    oq = result.state.get("order_query")

    assert oq.degraded, "缺的那一路必须如实记下来"
    assert any("物流" in note for note in oq.degraded)
    assert oq.logistics is None

    # 其余两路不受影响
    assert oq.order_id == _ORDER_NO
    assert oq.status_text == "已发货"
    assert oq.pay_amount == pytest.approx(459.00)
    assert oq.stocks and oq.stocks[0].sku_id == "SKU-KB80"

    assert result.task_result.task_result_code == 0
    # 客户要被告知缺了一块，而不是看到一个「查完了」的假象
    assert oq.degraded[0] in result.task_result.task_result


def test_inventory_failure_is_also_isolated(runner, monkeypatch):
    """库存这一路挂了，物流仍然要有 —— 降级是「哪一路缺」，不是「全都不要」。"""
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "inventory")
    result = runner.start(f"帮我查一下订单 {_ORDER_NO} 到哪了")

    oq = result.state.get("order_query")
    assert any("库存" in note for note in oq.degraded)
    assert oq.stocks == []
    assert oq.logistics is not None and oq.logistics.tracking_no.startswith("SF")
    assert result.task_result.task_result_code == 0


def test_order_master_failure_is_a_real_failure(runner, monkeypatch):
    """订单主数据失败是真的失败：没有它，物流与库存查到也没有意义。"""
    monkeypatch.setenv("MOCK_FAIL_ACTIONS", "order")
    result = runner.start(f"帮我查一下订单 {_ORDER_NO} 到哪了")

    oq = result.state.get("order_query")
    assert oq.degraded
    assert result.task_result.task_result_code == 1
    assert "没有查到" in result.task_result.task_result
    assert oq.logistics is None


def test_missing_trace_is_a_business_answer_not_an_outage(runner):
    """「查不到轨迹」≠「接口挂了」：一个是确定结论，一个是故障，措辞必须分开。

    该订单（已签收）在种子里没有物流记录，链路应当给出「暂未查到」的确定答复，
    而不是把物流说成「不可用」——两者对客户的下一步动作完全不同。
    """
    result = runner.start(f"帮我查一下订单 {_ORDER_NO_NO_TRACE} 到哪了")

    oq = result.state.get("order_query")
    assert oq.order_id == _ORDER_NO_NO_TRACE
    assert oq.degraded, "查不到轨迹也要留下一句说明"
    note = " ".join(oq.degraded)
    assert "暂未查到" in note
    assert "不可用" not in note
    assert result.task_result.task_result_code == 0
