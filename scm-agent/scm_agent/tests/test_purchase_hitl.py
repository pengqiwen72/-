"""
守的是采购链路的 HITL 全路径（`sub_purchase_request` 子图 + `integrations/policy.py`）。

五条性质，一条一测
------------------
  1. **免审批直采**：命中 PUR-005（小额）的方案不停，一步到执行；
  2. **需审批的方案必须真的停**：8000kg 走新供应商 S012 → PUR-002 →
     `interrupt` 弹出审批卡，卡上带完整依（金额/供应商/命中条款/推理轨迹）；
  3. **三段结论**：ok 落库 / discard 不落（方案标记 rejected，执行节点不做
     任何写操作）/ recreate 按 patch 修改要素**重走策略引擎**；
  4. **recreate 是「一次通过率」机制的唯一可演示点**：把供应商改成 S001
     重报 → 复判不命中审批条款 → 免审批直接下单（8000×5.80=46400）；
     patch 缺席时原样复判、二轮中断；
  5. **失败方案止步于 END**：这是一条修复的回归——失败方案的 status 曾写成
     "matched"，在条件边眼里和「免审批方案」一模一样，被路由到执行节点
     「下单」并写了空幂等记录。现在 failed 直接路由到 END。

用例全程走 `runner`（进程内、离线、mock 端口），只驱动图与记账，不碰网络。
"""

from __future__ import annotations

from integrations.idempotency import IdempotencyGuard

#: 免审批场景：S001（非新供应商、交期 7 天、报价 5.80），3000kg ≤ 5000 → PUR-005
#: 措辞用「指定 S001」而不是「供应商用 S001」：后者带「供应商」三个字，
#: 会与 supplier_query 的关键词表撞车、把整句拽去比价子图。提取器只认
#: 裸的 S 码正则，任何提到编码的措辞都抽得到，所以选词上避开即可。
AUTO_REQUEST = "给 WH01 申购 3000kg 冷轧钢板 1.2mm，指定 S001"

#: 审批场景：S012 是新供应商（报价 5.60），8000kg×5.60=44800 → PUR-002
APPROVAL_REQUEST = "给 WH01 申购 8000kg 冷轧钢板 1.2mm，指定 S012，产线急用"

#: 同一条申请、去掉「急用」的尾巴（四要素与审批场景相同 → 同一个申请单号）
RECREATE_REQUEST = "给 WH01 申购 8000kg 冷轧钢板 1.2mm，指定 S012"


# ==================== 1. 免审批直采 ====================

def test_auto_approved_request_goes_straight_to_execute(runner):
    """3000kg 命中「小额免审直采」：不中断，直接下单。"""
    result = runner.start(AUTO_REQUEST)
    assert result.error is None
    assert result.interrupted is False, "免审批方案不该停"

    plan = result.state.get("purchase_request")
    assert plan.status == "executed"
    assert plan.need_approval is False
    assert plan.policy_id == "PUR-005"
    assert plan.supplier_code == "S001"
    assert plan.unit_price == 5.80
    assert plan.amount == 17400.0
    assert plan.po_no, "免审批直采也要拿到采购单号"
    assert result.task_result.task_result_code == 0


# ==================== 2. 需审批的方案真的停住 ====================

def test_new_supplier_request_interrupts_with_full_evidence(runner):
    """S012 首单 → PUR-002 → 弹出审批卡；卡上能看到「为什么没走大额条款」。"""
    started = runner.start(APPROVAL_REQUEST)
    assert started.error is None
    assert started.interrupted, "新供应商首单必须先停在主管审批"

    payload = started.interrupts[0].value
    assert payload["type"] == "purchase_approval"
    assert payload["policy_id"] == "PUR-002"
    assert payload["material_code"] == "M100023"
    assert payload["supplier_code"] == "S012"
    assert payload["qty"] == 8000
    assert payload["unit_price"] == 5.60
    assert payload["amount"] == 44800.0
    assert payload["revision"] == 0
    assert "新供应商" in payload["reason"]

    # 轨迹是条件级的证据：命中条款的条件逐条（✓），被跳过的更高优先级条款
    # 带编号留痕（✗）—— 「为什么这单没走大额条款」的答案就在其中
    trace = " ".join(payload["reason_trace"])
    assert "新供应商" in trace
    assert "PUR-001" in trace, "被跳过的更高优先级条款也要留痕（44800 < 50000）"
    assert "S012" in trace, "取价依据（按指定供应商报价）要进轨迹"


def test_approving_executes_the_plan_seen_on_the_card(runner):
    """approve → 由执行节点下单，落库的就是卡上那一版（plan_id 一致）。"""
    started = runner.start(APPROVAL_REQUEST)
    payload = started.interrupts[0].value

    resumed = runner.resume(started.thread_id, "ok")
    assert resumed.error is None
    assert resumed.interrupted is False

    plan = resumed.state.get("purchase_request")
    assert plan.status == "executed"
    assert plan.plan_id == payload["plan_id"]
    assert plan.biz_id == payload["biz_id"]
    assert plan.amount == 44800.0
    assert plan.po_no

    # 审批记录记了一笔 approved（第 0 轮，键不带后缀）
    guard = IdempotencyGuard()
    record = guard.get_approval(IdempotencyGuard.approval_key(started.thread_id, plan.plan_id))
    assert record is not None and record["decision"] == "approved"


def test_rejecting_leaves_no_write_operation(runner):
    """discard → 方案标记 rejected，执行节点不做任何写操作，但仍要如实答复。"""
    started = runner.start(APPROVAL_REQUEST)
    resumed = runner.resume(started.thread_id, "discard")

    plan = resumed.state.get("purchase_request")
    assert plan.status == "rejected"
    assert plan.po_no == "", "驳回的申请不能留下采购单号"
    assert "驳回" in plan.message

    stages = [ev.get("stage") for ev in resumed.events]
    assert "purchase_submitted" not in stages, "驳回路径绝不能到达下单动作"
    assert "hitl_rejected" in stages, "时间线上要如实标出「驳回、未执行」"
    assert resumed.task_result.task_result_code == 0, "驳回是正常业务结论，不是系统失败"


# ==================== 3/4. recreate：改判重报、重走策略 ====================

def test_recreate_with_patch_rejudges_and_bypasses_approval(runner):
    """一次通过率的完整故事：驳回带修改重报，复判免审批直接下单。

    S012 → S001 的 patch 落进方案后：8000×5.80=46400 < 50000、非新供应商、
    高于均价 5.33% < 15%、交期 7 天 —— 逐条不再命中，落进兜底常规流程。
    """
    started = runner.start(RECREATE_REQUEST)
    assert started.interrupted
    first = started.interrupts[0].value
    assert first["policy_id"] == "PUR-002"

    resumed = runner.resume(started.thread_id, {
        "decision": "recreate",
        "patch": {"supplier_code": "S001"},
    })
    assert resumed.error is None
    assert resumed.interrupted is False, "复判免审批后不该再停"

    plan = resumed.state.get("purchase_request")
    assert plan.status == "executed"
    assert plan.revision == 1, "重报轮次要留痕"
    assert plan.supplier_code == "S001", "patch 要真的落到方案上"
    assert plan.unit_price == 5.80
    assert plan.amount == 46400.0
    assert plan.policy_id == "PUR-000", "复判后不再命中专项审批条款"
    assert plan.po_no

    assert any(t.startswith("↻") for t in plan.reason_trace), "重报轮次要进推理轨迹"
    stages = [ev.get("stage") for ev in resumed.events]
    assert "hitl_recreate" in stages

    # 第一轮记的是 recreated（不是 approved/rejected）——首次通过率靠它算
    guard = IdempotencyGuard()
    record = guard.get_approval(
        IdempotencyGuard.approval_key(started.thread_id, plan.plan_id, 0)
    )
    assert record is not None and record["decision"] == "recreated"


def test_recreate_without_patch_reinterrupts_in_round_two(runner):
    """patch 缺席 = 原样复判：仍命中 PUR-002 → 二轮中断，绝不是静默放行。"""
    started = runner.start(RECREATE_REQUEST)
    first = started.interrupts[0].value

    second = runner.resume(started.thread_id, {"decision": "recreate"})
    assert second.interrupted, "原样复判仍要审批，必须再中断一轮"
    payload = second.interrupts[0].value
    assert payload["revision"] == 1
    assert payload["plan_id"] == first["plan_id"], "biz_id 没变，方案锚点也不变"
    assert "未带修改" in " ".join(payload["reason_trace"])

    # 二轮批准：按 S012 原方案（44800）执行；两轮审批各记一笔
    final = runner.resume(second.thread_id, "ok")
    plan = final.state.get("purchase_request")
    assert plan.status == "executed"
    assert plan.revision == 1
    assert plan.supplier_code == "S012"
    assert plan.amount == 44800.0

    guard = IdempotencyGuard()
    assert guard.get_approval(
        IdempotencyGuard.approval_key(second.thread_id, plan.plan_id, 0)
    )["decision"] == "recreated"
    assert guard.get_approval(
        IdempotencyGuard.approval_key(second.thread_id, plan.plan_id, 1)
    )["decision"] == "approved"


def test_patch_unknown_fields_are_ignored_and_traced(runner):
    """patch 只认白名单字段：未知键忽略并留痕，但不废掉整轮审批。"""
    started = runner.start(RECREATE_REQUEST)
    resumed = runner.resume(started.thread_id, {
        "decision": "recreate",
        "patch": {"supplier_code": "S001", "unit_price": 1.0, "material_code": "M100031"},
    })

    plan = resumed.state.get("purchase_request")
    assert plan.material_code == "M100023", "物料是申请要素，patch 改不了"
    assert plan.unit_price == 5.80, "单价由报价决定，patch 改不了"
    assert plan.supplier_code == "S001", "合法字段照常生效"
    trace = " ".join(plan.reason_trace)
    assert "unit_price" in trace and "忽略" in trace
    assert "material_code" in trace


# ==================== 5. 失败方案止步于 END ====================

def test_menu_id_direct_without_content_stops_before_execute(runner):
    """菜单 id 直达但没带申请内容：失败方案必须路由到 END。

    回归测试：失败方案曾经的 status 是 "matched"，在条件边眼里和「免审批
    方案」一模一样 —— 被路由进执行节点「下单」（拿空要素），还写了一条
    空的幂等记录。现在 `_failure` 写 "failed"，条件边见 failed 直接结束。
    """
    result = runner.start("purchase_request")
    assert result.error is None

    plan = result.state.get("purchase_request")
    assert plan.status == "failed"
    assert plan.po_no == ""

    executed = [u for u in result.updates if "purchase_request_execute_node" in u]
    assert not executed, "失败方案绝不能流过执行节点"
    stages = [ev.get("stage") for ev in result.events]
    assert "purchase_submitted" not in stages
    assert result.task_result.task_result_code == 1


# ==================== 幂等：同输入两次，同号且第二次短路 ====================

def test_same_input_twice_gets_same_biz_id_and_second_run_short_circuits(runner):
    """同一笔申购提交两次：单号一致，第二次命中幂等短路，不产生第二张订单。"""
    first = runner.start(AUTO_REQUEST)
    assert first.error is None
    plan_first = first.state.get("purchase_request")
    assert plan_first.status == "executed"
    assert plan_first.po_no

    second = runner.start(AUTO_REQUEST)
    plan_second = second.state.get("purchase_request")
    assert plan_second.status == "replayed"
    assert plan_second.replayed is True
    assert plan_second.biz_id == plan_first.biz_id, "同输入必须同号（确定性单号）"
    assert plan_second.plan_id == plan_first.plan_id
    assert plan_second.po_no == plan_first.po_no, "不许产生第二个采购单号"

    stages = [ev.get("stage") for ev in second.events]
    assert "idempotent_replay" in stages


# ==================== 同号不同要素：冲突转人工 ====================

def test_reissuing_the_same_request_with_new_elements_becomes_conflict(runner):
    """同一笔申请（同单号）换要素再提：不静默复用、也不重复下单，转人工核实。

    这是「同号」与「同内容」两件事的交界：单号哈希只含物料/数量/到货仓/提报人，
    换供应商不改单号；但要素变了就不是重放 —— guard 判冲突，执行节点什么都不写。
    演示上它出现在「S012 版本批过一单、同一笔又改判重报 S001 版本」的连播里；
    quickstart 每个场景各自用干净的 store，就是为了不让这种跨场景串号发生，
    把「改判重报」场景的故事留在它自己该有的样子。
    """
    first = runner.start(APPROVAL_REQUEST)
    first = runner.resume(first.thread_id, "ok")
    plan_first = first.state.get("purchase_request")
    assert plan_first.status == "executed"

    second = runner.start(RECREATE_REQUEST)
    assert second.interrupted
    second = runner.resume(second.thread_id, {
        "decision": "recreate",
        "patch": {"supplier_code": "S001"},
    })

    plan_second = second.state.get("purchase_request")
    assert plan_second.status == "conflict"
    assert plan_second.biz_id == plan_first.biz_id, "同号：单号哈希不含供应商"
    assert plan_second.po_no == "", "冲突不能产生第二张采购订单"
    assert "转人工" in plan_second.message

    stages = [ev.get("stage") for ev in second.events]
    assert "idempotent_conflict" in stages, "冲突与重放是两回事，时间线上要分开"
    assert "purchase_submitted" not in stages, "冲突路径绝不能到达下单动作"
    assert second.task_result.task_result_code == 0, "转人工是业务结论，不是系统失败"
