"""
守的是「幂等三重防护」这条线（`src/integrations/idempotency.py`）。

把采购订单下出去是唯一会产生真金白银的操作，而它的触发路径都容易重复：
员工连点两次、HITL 恢复时节点被重放、SSE 断线重连。三种情形必须分得清清楚楚：

  1. **同键同内容** -> 短路复用首次结果（`replayed=True`），不再调下游；
  2. **同键不同内容** -> 抛 `IdempotencyConflict`，拒绝执行并转人工 ——
     数量从 3000 改成 5000，静默按 3000 下单会少买，直接下第二次会重复采购，
     两者都赌不起；
  3. **不同键**（换申请单或换动作）-> 各自独立成立，互不干扰。

另外三条边界：`plan_id` 必须由 `order_id+action_type` **确定性**生成
（它是所有记账的锚点）；业务单号由要素哈希确定性生成（同一笔申请重发多少次
都是同一个号）；失败记录不能写成「完成」（否则一次接口超时会让这笔申请
永远下不出去）。

用例只驱动 guard 与图的 mock 端口，不触碰任何真实的下单接口。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from langgraph.store.memory import InMemoryStore

from integrations.idempotency import (
    IdempotencyConflict,
    IdempotencyGuard,
    build_key,
    build_plan_id,
    build_receipt_id,
    build_request_id,
    request_hash,
)

ORDER = "PR20261008000001"
ACTION = "purchase_request"
PAYLOAD = {"order_id": ORDER, "action_type": ACTION, "amount": 56000.0}


@pytest.fixture
def guard():
    """每次用例一个独立的记账本（InMemoryStore），用例之间不共享记录。"""
    return IdempotencyGuard(store=InMemoryStore())


# ==================== 锚点：确定性的 plan_id ====================

def test_plan_id_is_deterministic_and_keyed_by_order_and_action():
    """同一个「业务单号 + 动作」永远算出同一个 plan_id；换任一项就换一个。"""
    first = build_plan_id(ORDER, ACTION)
    assert first == build_plan_id(ORDER, ACTION)
    assert len(first) == 16

    assert build_plan_id(ORDER, "goods_receipt") != first
    assert build_plan_id("PR20261008000002", ACTION) != first


def test_business_key_is_order_plus_action():
    assert build_key(ORDER, ACTION) == f"{ORDER}:{ACTION}"
    assert build_key(ORDER, ACTION) != build_key(ORDER, "goods_receipt")


def test_request_hash_ignores_key_order_but_not_content():
    """键序不算差异（不同调用路径 dict 顺序可能不同），内容变了才算。"""
    assert request_hash({"a": 1, "b": 2}) == request_hash({"b": 2, "a": 1})
    assert request_hash({"a": 1}) != request_hash({"a": 1.01})


# ==================== 情形一：同键同内容 -> 短路 ====================

def test_same_key_same_payload_replays_the_first_result(guard):
    """首次执行完成后，同键同内容再来一次：拿回首次结果，不再执行。"""
    record = guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    assert record.status == "in_progress"
    assert record.replayed is False, "首次请求不是重放"
    assert record.plan_id == build_plan_id(ORDER, ACTION)

    guard.complete(record.key, {"po_no": "PO-TEST0001"})

    again = guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    assert again.replayed is True
    assert again.status == "done"
    assert again.result["po_no"] == "PO-TEST0001"
    assert again.plan_id == record.plan_id

    # 记账只有一条：短路靠的是「同一把钥匙」，不是又写了一条新记录
    assert guard.get(record.key).request_hash == record.request_hash


def test_in_progress_same_key_is_not_treated_as_replayed(guard):
    """还没完成的记录不算重放 —— 不能拿一个空结果去糊弄调用方。"""
    guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    again = guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    assert again.status == "in_progress"
    assert again.replayed is False


# ==================== 情形二：同键不同内容 -> 冲突 ====================

def test_same_key_different_payload_raises_conflict(guard):
    """金额从 56000 改成 90000（数量随之从 3000 变成 5000）：这不是重放，必须拒绝并转人工。"""
    guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)

    with pytest.raises(IdempotencyConflict) as excinfo:
        guard.begin(
            order_id=ORDER, action_type=ACTION,
            payload={**PAYLOAD, "amount": 90000.0},
        )

    conflict = excinfo.value
    assert conflict.key == f"{ORDER}:{ACTION}"
    assert conflict.first_hash != conflict.new_hash

    # 首次记录不被覆盖：后来的「不同请求」不能篡改已有的账
    still = guard.get(conflict.key)
    assert still.request_hash == conflict.first_hash
    assert still.status == "in_progress"


# ==================== 情形三：不同键各自成立 ====================

def test_different_actions_or_orders_are_independent(guard):
    """换动作 / 换申请单就是另一笔业务，各自成立、互不冲突。"""
    first = guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    second = guard.begin(
        order_id=ORDER, action_type="goods_receipt",
        payload={"order_id": ORDER, "action_type": "goods_receipt"},
    )
    third = guard.begin(
        order_id="PR20261008000002", action_type=ACTION,
        payload={**PAYLOAD, "order_id": "PR20261008000002"},
    )

    assert len({first.key, second.key, third.key}) == 3
    assert len({first.plan_id, second.plan_id, third.plan_id}) == 3

    guard.complete(second.key, {"receipt_id": "RC-TEST0001"})
    assert guard.get(second.key).replayed is True
    # 另一把钥匙不受影响
    assert guard.get(first.key).replayed is False
    assert guard.get(third.key).replayed is False


# ==================== 失败与审批的边界 ====================

def test_failed_attempt_stays_retryable(guard):
    """失败不能写进「完成」：一次接口超时不该让这笔申请永远下不出去。"""
    record = guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    guard.fail(record.key, "上游超时")

    failed = guard.get(record.key)
    assert failed.status == "failed"
    assert failed.replayed is False

    retry = guard.begin(order_id=ORDER, action_type=ACTION, payload=PAYLOAD)
    assert retry.replayed is False, "失败后重试必须真的重新执行"
    assert retry.status == "failed"


def test_approval_record_is_written_only_once(guard):
    """审批节点会被重放：第一次批准记一笔，第二遍重放不该再记。"""
    assert guard.record_approval(
        thread_id="thread-1", plan_id="plan-1", decision="approved", operator="OP002",
    ) is True
    assert guard.record_approval(
        thread_id="thread-1", plan_id="plan-1", decision="approved", operator="OP002",
    ) is False, "重复写入必须被忽略"

    stored = guard.get_approval("thread-1:plan-1")
    assert stored["decision"] == "approved"
    assert stored["operator"] == "OP002"

    # 换线程或换方案就是另一笔审批，各自生效
    assert guard.record_approval(thread_id="thread-2", plan_id="plan-1", decision="approved") is True
    assert guard.record_approval(thread_id="thread-1", plan_id="plan-2", decision="approved") is True


def test_approval_records_are_kept_per_revision(guard):
    """改判重报的多轮结论各记一笔：第 0 轮不带后缀（兼容历史），之后带 `:r{n}`。

    同一轮内仍是首写胜（节点重放不重复记账），但跨轮次的结论不能互相吞掉 ——
    「首次通过率」（第 0 轮就通过的比例）正是靠两笔记录算出来的。
    """
    assert IdempotencyGuard.approval_key("t1", "p1") == "t1:p1"
    assert IdempotencyGuard.approval_key("t1", "p1", 0) == "t1:p1"
    assert IdempotencyGuard.approval_key("t1", "p1", 1) == "t1:p1:r1"

    assert guard.record_approval(
        thread_id="t1", plan_id="p1", decision="recreated", operator="OP002",
    ) is True
    assert guard.record_approval(
        thread_id="t1", plan_id="p1", decision="approved", operator="OP002", revision=1,
    ) is True
    # 同一轮重放：不记第二笔
    assert guard.record_approval(
        thread_id="t1", plan_id="p1", decision="approved", operator="OP002", revision=1,
    ) is False

    first_round = guard.get_approval("t1:p1")
    second_round = guard.get_approval("t1:p1:r1")
    assert first_round["decision"] == "recreated"
    assert first_round["revision"] == 0
    assert second_round["decision"] == "approved"
    assert second_round["revision"] == 1


# ==================== 锚点的锚点：确定性业务单号 ====================

def test_request_id_is_built_from_the_four_business_elements():
    """采购申请单号由「物料 + 数量 + 到货仓 + 提报人」四要素哈希而来。"""
    when = datetime(2026, 10, 8)
    first = build_request_id("M100023", 3000, "WH01", "OP001", when=when)
    assert first == build_request_id("M100023", 3000, "WH01", "OP001", when=when)
    assert first.startswith("PR20261008")
    assert len(first) == len("PR") + 8 + 6

    # 四要素改任何一项都是另一笔申请，理应换号
    assert build_request_id("M100031", 3000, "WH01", "OP001", when=when) != first
    assert build_request_id("M100023", 5000, "WH01", "OP001", when=when) != first
    assert build_request_id("M100023", 3000, "WH02", "OP001", when=when) != first
    assert build_request_id("M100023", 3000, "WH01", "OP002", when=when) != first


def test_receipt_id_is_built_from_the_order_and_the_counts():
    """收货单号由「订单 + 实收 + 不良」哈希而来；数量改了就是另一笔登记。"""
    when = datetime(2026, 10, 9)
    first = build_receipt_id("PO20260928002", 920, 30, when=when)
    assert first == build_receipt_id("PO20260928002", 920, 30, when=when)
    assert first.startswith("RC20261009")

    assert build_receipt_id("PO20260928001", 920, 30, when=when) != first
    assert build_receipt_id("PO20260928002", 950, 30, when=when) != first
    assert build_receipt_id("PO20260928002", 920, 25, when=when) != first


# ==================== 整链路：同一诉求提交两次 ====================

def test_resubmitting_the_same_request_creates_only_one_purchase_order(runner):
    """同一笔申购提交两次：只有一个采购单号，第二次明确标记为命中幂等。"""
    # 显式指定 S001：不指定会走「自动选最低价」选中交期 2 天的供应商，
    # 命中「交期过短需核实」而转入审批 —— 这条用例要的是免审批直采链路。
    request = "给 WH01 申购 3000kg 冷轧钢板 1.2mm，指定 S001"

    first = runner.start(request)
    assert first.error is None
    plan_first = first.state.get("purchase_request")
    assert plan_first.status == "executed"
    assert plan_first.replayed is False
    assert plan_first.po_no, "首次执行应当拿到采购单号"

    second = runner.start(request)
    assert second.error is None
    plan_second = second.state.get("purchase_request")

    assert plan_second.status == "replayed"
    assert plan_second.replayed is True
    assert plan_second.po_no == plan_first.po_no, "不许产生第二个采购单号"
    assert plan_second.plan_id == plan_first.plan_id

    # 短路这件事要能在时间线上看见（工作台靠它解释「为什么没有新单号」）
    stages = [ev.get("stage") for ev in second.events]
    assert "idempotent_replay" in stages
    assert second.task_result.task_result_code == 0
