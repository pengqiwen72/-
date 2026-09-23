#!/usr/bin/env python
"""
零配置演示入口 —— 不启动任何服务，直接跑通智能体全链路。

    python quickstart.py              # 交互模式：跑一遍，遇到审批停下来问你
    python quickstart.py --yes        # 非交互：审批一律自动通过（CI 用）
    python quickstart.py --list       # 列出所有场景
    python quickstart.py -s tag       # 只跑指定场景

设计要点
--------
* **不联网**：默认 LLM_MODE=offline + INTEGRATION_MODE=mock，所有外部依赖
  都有内置替代实现，`git clone` 之后直接就能跑。
* **不启服务**：用 InMemorySaver 替代 LangGraph 平台运行时，全部在进程内完成。
* 与 webapp 的 SSE 接口**共用同一套进度事件**（observability/progress.py），
  所以这里看到的过程和线上是同一个。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ---- 让脚本能直接跑：把 src 挂到 sys.path，并把默认值钉在离线模式 ----
_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("LLM_MODE", "offline")
os.environ.setdefault("INTEGRATION_MODE", "mock")
os.environ.setdefault("TOOL_GATEWAY", "direct")


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文日志会乱码甚至抛异常。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


# ==================== 场景定义 ====================

SCENARIOS: dict[str, dict] = {
    "tag": {
        "title": "生成客户标签",
        "input": "给这个客户打个标签",
        "desc": "走标签子图，出建议后等人工审批，可确认/放弃/重新生成。",
    },
    "profile": {
        "title": "生成客户画像",
        "input": "帮我更新一下客户画像",
        "desc": "走画像子图，从对话与订单中抽取画像项，同样带人工审批。",
    },
    "schedule": {
        "title": "创建跟进日程",
        "input": "给客户安排一个回访日程",
        "desc": "走日程子图，生成跟进建议并创建日程（mock 不真的建日程）。",
    },
    "chat": {
        "title": "坐席话术建议",
        "input": "帮我看看怎么回复客户",
        "desc": "走话术建议子图，结合历史对话生成回复建议。",
    },
    "talk": {
        "title": "闲聊兜底",
        "input": "你好呀",
        "desc": "没有明确意图时走通用问答，说明自身能力边界。",
    },
}

DEFAULT_ORDER = ["talk", "chat", "tag", "schedule", "profile"]


# ==================== 输出 ====================

def _hr(char: str = "─", width: int = 68) -> str:
    return char * width


def _print_events(events: list[dict]) -> None:
    """把进度事件渲染成一行行带缩进的阶段。"""
    for ev in events:
        stage = ev.get("stage", "?")
        label = ev.get("label", STAGE_LABELS.get(stage, stage))
        detail = ev.get("detail", "")
        indent = "  " + "  " * int(ev.get("depth", 0))
        line = f"{indent}· {label}"
        if detail:
            line += f"：{detail}"
        print(line)


def _print_node_trace(updates: list[dict]) -> None:
    """没有进度事件时，从 updates 通道还原节点执行轨迹。

    这是「零改造成本」的进度来源：`stream_mode="updates"` 每个 chunk 以节点名
    为键，按到达顺序打印就是执行顺序。等各节点接上 `emit()` 之后，
    `_print_events` 会给出更细的语义（检索命中、政策匹配、幂等短路等）。
    """
    for chunk in updates:
        if not isinstance(chunk, dict):
            continue
        for node_name in chunk:
            print(f"    ↳ {node_name}")


def _print_progress(result) -> None:
    """优先用语义化进度事件，否则退回节点轨迹。"""
    if result.events:
        _print_events(result.events)
    elif result.updates:
        _print_node_trace(result.updates)
    else:
        print("    （无进度输出）")


def _print_state_summary(state: dict) -> None:
    """打印本次运行的业务产出。"""
    node_results = state.get("node_result") or []
    if node_results:
        print("\n  执行轨迹：")
        for nr in node_results:
            mark = "✓" if getattr(nr, "execute_result_code", 1) == 0 else "✗"
            name = getattr(nr, "execute_node_name", "?")
            msg = getattr(nr, "execute_result_msg", "")
            print(f"    {mark} {name:<34} {msg}")

    task_result = state.get("task_result")
    if task_result is not None:
        msg = getattr(task_result, "task_result", task_result)
        print(f"\n  最终结果：{msg}")

    suggestion = state.get("suggestion_tag")
    if suggestion is not None:
        print(f"\n  标签建议：{suggestion.model_dump_json(indent=2)}")

    suggestion = state.get("suggestion_profile")
    if suggestion is not None:
        print(f"\n  画像建议：{suggestion.model_dump_json(indent=2)}")


def _ask_approval(interrupts: list, auto_yes: bool) -> str:
    """处理人工审批。返回回给图的决策值。"""
    payload = interrupts[0].value if interrupts else {}
    if isinstance(payload, dict):
        desc = payload.get("description", "需要你确认")
        print(f"\n  ⏸  等待人工审批：{desc}")
        for key in ("old_tags", "new_tags", "old_profile", "new_profile"):
            if payload.get(key):
                print(f"      {key}: {payload[key]}")
    else:
        print(f"\n  ⏸  等待人工审批：{payload}")

    if auto_yes:
        print("  → 非交互模式，自动确认（ok）")
        return "ok"

    print("\n  请选择：[1] 确认  [2] 放弃  [3] 重新生成")
    try:
        choice = input("  > ").strip()
    except EOFError:  # pragma: no cover - 无 stdin 时
        print("  （读不到输入，视为确认）")
        return "ok"

    return {"1": "ok", "2": "discard", "3": "recreate"}.get(choice, "ok")


# ==================== 场景执行 ====================

def run_scenario(key: str, *, auto_yes: bool = False) -> int:
    """跑一个场景。返回退出码（0 成功）。"""
    from demo import DemoRunner, build_demo_graph
    from observability.progress import STAGE_LABELS

    spec = SCENARIOS[key]
    print(f"\n{_hr('═')}")
    print(f"  场景：{spec['title']}  [{key}]")
    print(f"  输入：{spec['input']!r}")
    print(f"  说明：{spec['desc']}")
    print(_hr("═"))

    runner = DemoRunner(build_demo_graph())
    result = runner.start(spec["input"])

    print("\n  执行过程：")
    _print_progress(result)

    # 最多处理 3 轮审批，避免「重新生成」无限循环
    rounds = 0
    while result.interrupted and rounds < 3:
        rounds += 1
        decision = _ask_approval(result.interrupts, auto_yes)
        result = runner.resume(result.thread_id, decision)
        print("\n  审批后过程：")
        _print_progress(result)

    if result.error:
        print(f"\n  ✗ 执行出错：{result.error}")
        return 1

    if result.interrupted:
        print("\n  ⚠ 审批轮次用尽，图仍处于中断状态")
        return 1

    _print_state_summary(result.state)
    print(f"\n  线程：{result.thread_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8()

    parser = argparse.ArgumentParser(
        prog="quickstart.py",
        description="电商智能客服与运营助手 —— 离线零配置演示",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="列出所有场景")
    parser.add_argument("-s", "--scenario", action="append", help="只跑指定场景（可重复）")
    parser.add_argument("--yes", "-y", action="store_true", help="非交互：审批自动通过")
    args = parser.parse_args(argv)

    if args.list:
        print("\n可用场景：")
        for key, spec in SCENARIOS.items():
            print(f"  {key:<10} {spec['title']:<14} {spec['desc']}")
        print()
        return 0

    keys = args.scenario or DEFAULT_ORDER
    unknown = [k for k in keys if k not in SCENARIOS]
    if unknown:
        print(f"未知场景：{', '.join(unknown)}（用 --list 查看可用场景）", file=sys.stderr)
        return 2

    print(f"\n{_hr('═')}")
    print("  电商智能客服与运营助手 —— 离线演示")
    print(f"  模式：LLM_MODE={os.environ['LLM_MODE']}  "
          f"INTEGRATION_MODE={os.environ['INTEGRATION_MODE']}  （全程不联网）")
    print(_hr("═"))

    failed = 0
    for key in keys:
        try:
            failed += run_scenario(key, auto_yes=args.yes) != 0
        except KeyboardInterrupt:
            print("\n\n已中断。")
            return 130

    print(f"\n{_hr('═')}")
    print(f"  完成：{len(keys) - failed}/{len(keys)} 个场景通过")
    print(_hr("═"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
