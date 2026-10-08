"""
守的是「HITL 恢复时身份必须由本次调用重新带回来」这条线
（`src/demo/runner.py` + `src/graphs/scm_graph/scm_node_load_data.py`）。

为什么单独立一个文件
--------------------
`interrupt()` 把图暂停在检查点里；恢复时 LangGraph 只还原**状态**，而
`config["configurable"]` 是**每次调用现传的** —— `user_id` 不在状态里，
也就不随检查点回来。它偏偏是装载节点的硬输入（`load_operator_info_node`
直接读 `config["configurable"]["user_id"]`）：漏了就是 KeyError，员工在界面上
看到的是「点了确认没反应」—— 最难排查的一类故障。

供应链域里 `external_id` 不再承载业务含义，但**配置里仍保留这个键**：
ConfigurableField 是两个键的契约，少一个下游节点就 KeyError（历史踩过）。

三条要守住的性质：
  1. 恢复复用同一份 `thread_config`；缺身份 / 缺线程必须**报错**，而不是静默成功；
  2. 操作员身份经装载节点进入状态，也进入确定性单号 —— 同一句申购，换个提报人
     就是另一笔申请；同一个人重发多少次，还是同一个号；
  3. 人工在中断载荷里看到的方案，就是恢复后真正落库的那一版（同一线程的状态
     在两段之间没有丢）。

用例走的是工作台的真实三拍：`runner.start` → 弹审批 → `runner.resume`，
全程进程内、不碰网络。
"""

from __future__ import annotations

import pytest

from demo.runner import DEFAULT_EXTERNAL_ID, DEFAULT_USER_ID, DemoRunner, RunResult

# purchase_approval 场景：金额越过审批线，停在主管审批
TASK = "给 WH01 申购 10000kg 冷轧钢板 1.2mm，产线急用"


def _begin(runner: DemoRunner) -> RunResult:
    """发起任务，停在主管审批 —— 本文件所有断言都以这张审批卡为锚点。"""
    started = runner.start(TASK)
    assert started.error is None
    assert started.interrupted, "大额申购必须先停在人工审批，否则后面的断言没有意义"
    return started


def _approval_payload(runner: DemoRunner) -> dict:
    return _begin(runner).interrupts[0].value


# ==================== 配置本身 ====================

def test_thread_config_carries_thread_identity_for_both_legs(runner):
    """发起与恢复共用一份 thread_config：thread_id + user_id 缺一不可。

    恢复时要**再构造一次**这份配置，所以同一线程两次构造必须完全一致 ——
    「点了确认没反应」这类故障就是从这里的差异开始的。
    """
    config = runner.thread_config("t-1")
    configurable = config["configurable"]

    assert configurable["thread_id"] == "t-1"
    assert configurable["user_id"] == DEFAULT_USER_ID
    # 两个键的契约：域内不用，但必须保留（少一个下游节点就 KeyError）
    assert configurable["external_id"] == DEFAULT_EXTERNAL_ID
    assert runner.thread_config("t-1") == config


def test_identity_is_not_part_of_the_checkpointed_state(runner):
    """前提检查：身份不在状态里 —— 所以它不可能靠检查点恢复，必须每次现传。

    这不是实现细节：若哪天有人把 user_id 塞进 State 图省事，恢复时会拿到
    **发起那次**的身份；多操作员场景下，落库的提报人就会错。
    """
    started = _begin(runner)
    state = runner.get_state(started.thread_id)

    assert state, "线程应当已有检查点内容"
    assert "user_id" not in state and "external_id" not in state


# ==================== 漏带身份：必须报错 ====================

def test_start_without_identity_fails_loudly(runner):
    """发起时配置缺 user_id：任务以 error 收尾，且报错要指名缺的字段。

    静默成功比报错可怕得多 —— 审批已点、结果没写、日志干净，
    排查时根本不知道从哪里下手。这里的「报错」是要求行为。
    """
    events = list(runner.stream(
        {"task_input": "你好呀"},
        {"configurable": {"thread_id": "t-no-identity"}},
    ))

    assert events[-1]["type"] == "error", "缺身份不可能完成数据装载"
    assert "user_id" in events[-1]["message"], "报错要指名缺的是哪个字段"


def test_resume_without_thread_id_fails_loudly(runner):
    """恢复时没有 thread_id：连线程都定位不到，直接抛错而不是静默无事发生。"""
    _begin(runner)  # 先真实地中断一次，证明恢复确实需要 thread_id

    with pytest.raises(KeyError):
        list(runner.stream(
            runner.resume_command("ok"),
            {"configurable": {"user_id": DEFAULT_USER_ID}},
        ))


# ==================== 身份进入确定性单号 ====================

def test_same_request_from_different_operators_gets_different_request_ids(runner):
    """同一句申购，不同提报人 = 两笔申请（提报人算进单号哈希）。

    两个人各自独立提了同样的需求，就是两笔业务；把身份排除在单号之外，
    第二个人的申请会静默命中第一个人的幂等记录、直接拿回「已下达」——
    他的需求被吞掉了。
    """
    first = _approval_payload(DemoRunner(runner.graph, user_id="OP001"))
    second = _approval_payload(DemoRunner(runner.graph, user_id="OP002"))

    assert first["biz_id"].startswith("PR")
    assert first["biz_id"] != second["biz_id"]


def test_same_operator_resubmitting_keeps_the_same_request_id(runner):
    """同一个人重复发起同一笔申请：单号必须一致，否则幂等就无从兜起。"""
    a = _approval_payload(DemoRunner(runner.graph, user_id="OP001"))
    b = _approval_payload(DemoRunner(runner.graph, user_id="OP001"))

    assert a["biz_id"] == b["biz_id"]
    assert a["plan_id"] == b["plan_id"]


# ==================== 带全身份：跑通且落库的就是卡上那一版 ====================

def test_resume_with_same_thread_config_applies_the_plan_seen_on_the_card(runner):
    """发起 → 中断 → 同一 thread_id + 同一身份恢复：跑通，且落库的就是卡上那一版。"""
    started = _begin(runner)
    payload = started.interrupts[0].value
    assert payload["type"] == "purchase_approval"
    assert payload["material_code"] == "M100023"

    resumed = runner.resume(started.thread_id, "ok")

    assert resumed.error is None
    assert resumed.interrupted is False, "恢复后应当越过中断点跑到结束"
    assert resumed.task_result.task_result_code == 0

    plan = resumed.state.get("purchase_request")
    assert plan.status == "executed"
    assert plan.plan_id == payload["plan_id"], "落库的必须是审批卡上那一版方案"
    assert plan.biz_id == payload["biz_id"]
    assert plan.po_no, "批准后应当拿到采购单号"


# ==================== 流式入口同样要带全 ====================

def test_streaming_resume_entry_carries_the_same_config(runner):
    """SSE 入口（stream + resume_command）自己拼配置，同样带全身份并以 done 收尾。"""
    started = _begin(runner)

    events = list(
        runner.stream(
            runner.resume_command("ok"),
            runner.thread_config(started.thread_id),
        )
    )

    assert all(ev["type"] != "error" for ev in events), events[-1]
    assert events[-1]["type"] == "done"
    assert events[-1]["thread_id"] == started.thread_id
    assert events[-1]["task_result"]["task_result_code"] == 0
