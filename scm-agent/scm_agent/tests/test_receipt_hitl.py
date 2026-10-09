"""
守的是收货链路的 HITL 全路径（`sub_goods_receipt` 子图 + `quality_policy.json`）。

与采购链路同型，但触发条件与复核对象不同：看的是**不良率**（这批货能不能收），
复核的产物是**处置方式**。四条性质各一测：

  1. **不超阈值不惊动人工**：QC-000 兜底 → 直接入库；
  2. **超阈值必须停**：3.26% 命中 QC-001 → 复核卡带实收/不良/不良率/命中依据；
  3. **处置随结论走**：approve → 认定「让步接收」；discard → 整批退货、
     不写 WMS；
  4. **patch 只改处置、改不了事实**：recreate 把处置改为「换货」→ 不良率
     是到货事实（patch 改不了）→ 复判仍命中 QC-001 → **二轮中断**（卡片上
     带着改后的处置）→ 确认后按「换货」执行。绕过事实的 patch 字段
     （qty_received 等）被忽略并留痕。

用例全程走 `runner`（进程内、离线、mock 端口）。
"""

from __future__ import annotations

from integrations.idempotency import IdempotencyGuard

#: 正常到货：实收 1000、不良 0 → QC-000 直接入库
NORMAL_REQUEST = "PO20260928002 到货了，实收 1000 支，没有发现问题"

#: 不良到货：实收 920、不良 30 → 3.26% → QC-001 复核
#: （剧情锚点：30/920 = 3.26% > 2%）
QUALITY_REQUEST = "PO20260928002 到货 920 支，开箱抽检发现 30 支磕碰变形"


# ==================== 1. 不超阈值不惊动人工 ====================

def test_normal_receipt_goes_straight_to_storage(runner):
    """不良率 0% 未命中 QC-001：不中断，直接办理入库。"""
    result = runner.start(NORMAL_REQUEST)
    assert result.error is None
    assert result.interrupted is False

    plan = result.state.get("goods_receipt")
    assert plan.status == "executed"
    assert plan.need_review is False
    assert plan.policy_id == "QC-000"
    assert plan.disposition == "入库"
    assert plan.receipt_no, "入库要有正式收货单号"
    assert result.task_result.task_result_code == 0


# ==================== 2. 超阈值必须停 ====================

def test_defect_over_threshold_interrupts_with_the_numbers(runner):
    """3.26% → QC-001 → 复核卡带实收 / 不良 / 不良率 / 判定依据。"""
    started = runner.start(QUALITY_REQUEST)
    assert started.error is None
    assert started.interrupted, "不良率超阈值必须先停在质检复核"

    payload = started.interrupts[0].value
    assert payload["type"] == "quality_review"
    assert payload["policy_id"] == "QC-001"
    assert payload["po_no"] == "PO20260928002"
    assert payload["qty_received"] == 920
    assert payload["qty_defect"] == 30
    assert payload["defect_rate"] == round(30 / 920, 4)
    assert payload["revision"] == 0
    assert payload["disposition"] == "待复核"

    trace = " ".join(payload["reason_trace"])
    assert "不良率 3.26% > 2.00%" in trace, "命中条款的条件要逐条进轨迹"
    assert "3.26%" in payload["reason"]


def test_review_approval_falls_back_to_compromise_acceptance(runner):
    """第一轮直接 approve：处置落定为让步接收（复审人对整批货放行）。"""
    started = runner.start(QUALITY_REQUEST)
    resumed = runner.resume(started.thread_id, "ok")
    assert resumed.error is None
    assert resumed.interrupted is False

    plan = resumed.state.get("goods_receipt")
    assert plan.status == "executed"
    assert plan.disposition == "让步接收"
    assert "让步接收" in plan.message
    assert plan.receipt_no

    guard = IdempotencyGuard()
    record = guard.get_approval(
        IdempotencyGuard.approval_key(started.thread_id, plan.plan_id)
    )
    assert record is not None and record["decision"] == "approved"


def test_rejecting_returns_the_whole_batch_without_wms_write(runner):
    """discard → 整批退货：方案标记 rejected，不提交 WMS 登记。"""
    started = runner.start(QUALITY_REQUEST)
    resumed = runner.resume(started.thread_id, "discard")

    plan = resumed.state.get("goods_receipt")
    assert plan.status == "rejected"
    assert plan.disposition == "退货"
    assert "拒收" in plan.message or "退货" in plan.message

    stages = [ev.get("stage") for ev in resumed.events]
    assert "receipt_submitted" not in stages, "驳回路径绝不能到达入库动作"
    assert "hitl_rejected" in stages, "时间线上要如实标出「驳回、未办理入库」"
    assert resumed.task_result.task_result_code == 0, "驳回是正常业务结论，不是系统失败"


# ==================== 3/4. recreate：处置可改、事实不可改 ====================

def test_recreate_with_disposition_patch_reinterrupts_then_executes_it(runner):
    """改判重报的完整两轮：处置改「换货」→ 复判仍要复核（不良率改不了）→
    第二轮确认后按「换货」执行。"""
    started = runner.start(QUALITY_REQUEST)
    first = started.interrupts[0].value

    second = runner.resume(started.thread_id, {
        "decision": "recreate",
        "patch": {"disposition": "换货"},
    })
    assert second.interrupted, "不良率是事实，复判必然仍命中 QC-001、再中断一轮"

    payload = second.interrupts[0].value
    assert payload["revision"] == 1
    assert payload["disposition"] == "换货", "第二轮卡片要带着改后的处置方式"
    assert payload["plan_id"] == first["plan_id"], "事实没变，方案锚点也不变"
    assert any(t.startswith("↻") for t in payload["reason_trace"])

    # 改判重报的事件发生在「处理 recreate 的那一次恢复」里 —— 它和最后批准
    # 是两段独立的运行，事件要在各自的 RunResult 上找
    stages = [ev.get("stage") for ev in second.events]
    assert "hitl_recreate" in stages

    final = runner.resume(second.thread_id, "ok")
    plan = final.state.get("goods_receipt")
    assert plan.status == "executed"
    assert plan.revision == 1
    assert plan.disposition == "换货", "approve 保留 patch 后的处置"
    assert "换货" in plan.message
    assert plan.receipt_no

    guard = IdempotencyGuard()
    assert guard.get_approval(
        IdempotencyGuard.approval_key(final.thread_id, plan.plan_id, 0)
    )["decision"] == "recreated"
    assert guard.get_approval(
        IdempotencyGuard.approval_key(final.thread_id, plan.plan_id, 1)
    )["decision"] == "approved"


def test_patch_cannot_change_received_quantities(runner):
    """实收 / 不良是到货事实（登记环节写下的数），patch 改不了、忽略并留痕。

    断言读第二轮的复核卡（interrupt payload）而不是父图状态：子图的输出
    要等子图跑完才并回父图，中断期间父图里那个字段还是 None —— 复核人
    在卡上看到的就是这一版，读卡也更贴近真实使用。
    """
    started = runner.start(QUALITY_REQUEST)
    resumed = runner.resume(started.thread_id, {
        "decision": "recreate",
        "patch": {"disposition": "让步接收", "qty_received": 500, "qty_defect": 0},
    })
    assert resumed.interrupted, "处置改了但事实没变，仍要再复核一轮"

    payload = resumed.interrupts[0].value
    assert payload["qty_received"] == 920, "实收不能被 patch 改动"
    assert payload["qty_defect"] == 30, "不良数不能被 patch 改动"
    assert payload["defect_rate"] == round(30 / 920, 4)
    assert payload["disposition"] == "让步接收", "合法字段照常生效"
    trace = " ".join(payload["reason_trace"])
    assert "qty_received" in trace and "忽略" in trace
    assert "qty_defect" in trace


# ==================== 失败方案止步于 END（与采购链路同型） ====================

def test_menu_id_direct_without_content_stops_before_execute(runner):
    """菜单 id 直达没带登记内容：失败方案路由到 END，不流进执行节点。"""
    result = runner.start("goods_receipt")
    assert result.error is None

    plan = result.state.get("goods_receipt")
    assert plan.status == "failed"
    assert plan.qty_received == 0

    executed = [u for u in result.updates if "goods_receipt_execute_node" in u]
    assert not executed, "失败方案绝不能流过执行节点"
    assert result.task_result.task_result_code == 1


# ==================== 幂等：同一批到货提交两次 ====================

def test_resubmitting_the_same_receipt_hits_idempotency(runner):
    """同一批到货登记两次：第二次命中幂等短路，不重复入库、单号一致。"""
    first = runner.start(NORMAL_REQUEST)
    plan_first = first.state.get("goods_receipt")
    assert plan_first.status == "executed"
    assert plan_first.receipt_no

    second = runner.start(NORMAL_REQUEST)
    plan_second = second.state.get("goods_receipt")
    assert plan_second.status == "replayed"
    assert plan_second.replayed is True
    assert plan_second.biz_id == plan_first.biz_id, "同输入必须同号（确定性单号）"
    assert plan_second.receipt_no == plan_first.receipt_no, "不许产生第二张收货单"

    stages = [ev.get("stage") for ev in second.events]
    assert "idempotent_replay" in stages


# ==================== 同批到货改判处置：冲突转人工 ====================

def test_same_arrival_reviewed_with_a_different_disposition_becomes_conflict(runner):
    """同一批到货先按「让步接收」入库，又改判「换货」重走一轮：处置变了、
    单号没变 → guard 判冲突，不产生第二张收货单。

    收货单号哈希取「订单 + 实收 + 不良」，不含处置方式 —— 复核结论怎么变
    都还是同一批货的登记。正因如此才要靠冲突检测兜住「同一批货两次入库」。
    """
    first = runner.start(QUALITY_REQUEST)
    first = runner.resume(first.thread_id, "ok")
    plan_first = first.state.get("goods_receipt")
    assert plan_first.status == "executed"
    assert plan_first.disposition == "让步接收"

    second = runner.start(QUALITY_REQUEST)
    assert second.interrupted
    second = runner.resume(second.thread_id, {
        "decision": "recreate",
        "patch": {"disposition": "换货"},
    })
    assert second.interrupted, "复判仍命中 QC-001，先再复核一轮"
    second = runner.resume(second.thread_id, "ok")

    plan_second = second.state.get("goods_receipt")
    assert plan_second.status == "conflict"
    assert plan_second.biz_id == plan_first.biz_id, "同号：单号哈希不含处置方式"
    assert plan_second.receipt_no == "", "冲突不能产生第二张收货单"
    assert "转人工" in plan_second.message

    stages = [ev.get("stage") for ev in second.events]
    assert "idempotent_conflict" in stages
    assert "receipt_submitted" not in stages, "冲突路径绝不能到达入库动作"
    assert second.task_result.task_result_code == 0, "转人工是业务结论，不是系统失败"
