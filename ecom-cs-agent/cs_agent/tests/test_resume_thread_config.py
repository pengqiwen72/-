"""
守的是「HITL 恢复时必须把身份（user_id / external_id）重新带回来」这条线
（`src/demo/runner.py` + `src/graphs/cs_graph/sub_tag/sub_tag_node.py`）。

为什么单独立一个文件
--------------------
`interrupt()` 把图暂停在检查点里；恢复时 LangGraph 只还原**状态**，而
`config["configurable"]` 是**每次调用现传的** —— `user_id` / `external_id`
不在状态里，也就不随检查点回来。偏偏恢复路径上的节点（`update_customer_tag`、
两个通知节点）直接读 `config["configurable"]["user_id"]`：
漏发就是 KeyError，任务卡死在审批按钮后面 —— 界面上表现是「点了确认没反应」，
恰恰是最难排查的一类故障。

三条要守住的性质：
  1. 恢复复用同一份 `thread_config`；漏带身份时必须**报错**，而不是静默成功；
  2. 恢复路径读的是**本次调用**的 config —— 换一个身份恢复，更新就落到换后的
     客户身上（如果哪天改成从检查点取，这里写死的会是发起时的客户）；
  3. 人工在中断载荷里看到的建议，就是恢复后真正落库的内容（同一线程的状态
     在两段之间没有丢）。

用例走的是工作台的真实三拍：`runner.start` → 弹审批 → `runner.resume`，
全程进程内、不碰网络。
"""

from __future__ import annotations

from demo.runner import DEFAULT_EXTERNAL_ID, DEFAULT_USER_ID, DemoRunner, RunResult

from tools import store_tool as st

# 种子数据里的两个服务关系：CS001 服务 wx_ext_001，CS002 服务 wx_ext_002
CUSTOMER_EXTERNAL_ID = "wx_ext_001"
FOLLOW_USER_ID = "CS001"
OTHER_CUSTOMER_EXTERNAL_ID = "wx_ext_002"
OTHER_FOLLOW_USER_ID = "CS002"

# 这句会命中标签链路并在人工确认处中断（会话里有刻字/时效的原始证据）
TASK = "给我的客户打一下标签，他最近问了很多刻字的事"


def _begin(runner: DemoRunner) -> RunResult:
    """发起任务，停在人工确认 —— 本文件所有用例的共同前提。"""
    started = runner.start(TASK)
    assert started.error is None
    assert started.interrupted, "标签链路必须先停在人工确认，否则后面的断言没有意义"
    return started


def _tags_of(external_id: str, follow_user_id: str) -> set[str]:
    return {t.tag_id for t in st.get_customer_tags(external_id, follow_user_id).customer_tags}


# ==================== 配置本身 ====================

def test_thread_config_carries_thread_identity_for_both_legs(runner):
    """发起与恢复共用一份 thread_config：thread_id + user_id + external_id 缺一不可。

    恢复时要**再构造一次**这份配置，所以同一线程两次构造必须完全一致 ——
    身份错位（把 A 客户的审批写到 B 客户头上）就是从这里的差异开始的。
    """
    config = runner.thread_config("t-1")
    configurable = config["configurable"]

    assert configurable["thread_id"] == "t-1"
    assert configurable["user_id"] == DEFAULT_USER_ID
    assert configurable["external_id"] == DEFAULT_EXTERNAL_ID
    assert runner.thread_config("t-1") == config


def test_identity_is_not_part_of_the_checkpointed_state(runner):
    """前提检查：身份不在状态里 —— 所以它不可能靠检查点恢复，必须每次现传。

    这不是实现细节：若哪天有人把 user_id 塞进 State 图省事，恢复时会拿到
    **发起那次**的身份；多坐席/多客户场景下，更新就会写到错误的客户身上。
    """
    started = _begin(runner)
    state = runner.get_state(started.thread_id)

    assert state, "线程应当已有检查点内容"
    assert "user_id" not in state and "external_id" not in state


# ==================== 漏带身份：必须报错 ====================

def test_resume_without_identity_fails_loudly(runner):
    """恢复时只给了 thread_id：任务报错，而不是静默成功。

    静默成功比报错可怕得多 —— 审批已点、结果没写、日志干净，
    排查时根本不知道从哪里下手。这里的「报错」是要求行为。
    """
    started = _begin(runner)
    bad_config = {"configurable": {"thread_id": started.thread_id}}

    events = list(runner.stream(runner.resume_command("ok"), bad_config))

    assert events[-1]["type"] == "error", "漏带身份不可能完成更新"
    assert "user_id" in events[-1]["message"], "报错要指名缺的是哪个字段"


# ==================== 带全身份：跑通且落到正确的客户身上 ====================

def test_resume_with_same_thread_config_completes_and_applies_the_approved_tags(runner):
    """发起 → 中断 → 同一 thread_id + 同一身份恢复：跑通，且审批的建议真的落了库。"""
    started = _begin(runner)

    approved = started.interrupts[0].value
    suggested_ids = {t.tag_id for t in approved["new_tags"].tag_ids_add}
    assert "TAG-BEHA-CUSTOM" in suggested_ids, "会话里有刻字诉求，审批卡片上应看到对应建议"

    resumed = runner.resume(started.thread_id, "ok")

    assert resumed.error is None
    assert resumed.interrupted is False, "恢复后应当越过中断点跑到结束"
    assert resumed.task_result.task_result_code == 0

    applied = _tags_of(CUSTOMER_EXTERNAL_ID, FOLLOW_USER_ID)
    assert suggested_ids <= applied, "人工确认的标签必须落到该客户身上"


def test_resume_reads_identity_from_this_call_not_from_the_checkpoint(runner):
    """同一 thread 换个身份恢复：更新落到**本次配置**指定的客户，而不是发起时的那个。

    这是「身份不随检查点恢复」最直接的反证 —— 若实现里偷用了检查点里的值，
    写在这里的一定还是 wx_ext_001。
    """
    started = _begin(runner)
    other = DemoRunner(
        runner.graph,
        user_id=OTHER_FOLLOW_USER_ID,
        external_id=OTHER_CUSTOMER_EXTERNAL_ID,
    )

    resumed = other.resume(started.thread_id, "ok")

    assert resumed.error is None
    assert resumed.task_result.task_result_code == 0
    assert "TAG-BEHA-CUSTOM" in _tags_of(OTHER_CUSTOMER_EXTERNAL_ID, OTHER_FOLLOW_USER_ID)
    assert "TAG-BEHA-CUSTOM" not in _tags_of(CUSTOMER_EXTERNAL_ID, FOLLOW_USER_ID), (
        "发起时的客户不该被这次恢复碰过"
    )


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
