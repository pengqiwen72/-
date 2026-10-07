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
# 按**脚本自身的位置**定位，不依赖 cwd —— 从仓库根、从 cs_agent、从任意目录
# 调用都成立（`python /path/to/ecom-cs-agent/quickstart.py` 也行）。
_ROOT = Path(__file__).resolve().parent          # 仓库根
_SRC = _ROOT / "cs_agent" / "src"
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

# 场景表长在 `demo/scenarios.py`：命令行与坐席工作台的 `/cs/samples` 共用同一份，
# 两处各写一遍迟早会漂移。从 `src` 导入，不依赖当前工作目录。
from demo.scenarios import DEFAULT_ORDER, SCENARIOS  # noqa: E402


# ==================== 输出 ====================

def _hr(char: str = "─", width: int = 68) -> str:
    return char * width


def _print_events(events: list[dict]) -> None:
    """把进度事件渲染成一行行带缩进的阶段。"""
    from observability.progress import STAGE_LABELS

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

    product_qa = state.get("product_qa")
    if product_qa is not None:
        print(f"\n  商品咨询：置信度 {product_qa.confidence:.2f}  "
              f"状态 {product_qa.status}  出处 {len(product_qa.citations)} 条")
        if product_qa.matched_terms:
            print(f"  命中词：{'、'.join(product_qa.matched_terms)}")
        if product_qa.reason:
            print(f"  转人工原因：{product_qa.reason}")

    order_query = state.get("order_query")
    if order_query is not None:
        print(f"\n  订单查询：{order_query.order_id or '—'}  {order_query.status_text or '—'}  "
              f"实付 {order_query.pay_amount:.2f} 元")
        if order_query.logistics is not None:
            trace = order_query.logistics
            print(f"  物流：{trace.carrier} {trace.tracking_no}  {trace.status}")
        if order_query.stocks:
            for stock in order_query.stocks:
                print(f"  库存：{stock.render()}")
        if order_query.degraded:
            print(f"  ⚠ 降级：{'；'.join(order_query.degraded)}")
        if order_query.normalize_trace:
            print("  归一轨迹：")
            for line in order_query.normalize_trace:
                print(f"    {line}")

    plan = state.get("after_sale")
    if plan is not None:
        print(f"\n  售后方案：{plan.plan_id or '—'}  政策 {plan.policy_id or '—'}（{plan.policy_title or '—'}）")
        print(f"  动作：{plan.action_type} / {plan.refund_type}  "
              f"金额 {plan.amount:.2f} 元  状态 {plan.status}"
              f"{'  审批人 ' + plan.approved_by if plan.approved_by else ''}")
        if plan.refund_id:
            print(f"  退款单号：{plan.refund_id}"
                  f"{'（命中幂等，未重复发起）' if plan.replayed else ''}")
        if plan.reason_trace:
            # 标记已由节点写进字符串，这里原样打印——见 sub_after_sale_node._build_plan
            print("  判定依据：")
            for line in plan.reason_trace:
                print(f"    {line}")
        if plan.reason:
            print(f"  方案说明：{plan.reason}")

    report = state.get("weekly_report")
    if report is not None:
        print(f"\n  数据周报（{report.period_start} ~ {report.period_end}）"
              f"{'  ⚠ 已降级' if report.degraded else ''}：")
        for line in (report.table or "").splitlines():
            print(f"    {line}")
        for anomaly in report.anomalies:
            print(f"    {anomaly.render()}")
        if report.summary:
            print(f"  摘要：{report.summary}")
        for highlight in report.highlights:
            print(f"    · {highlight}")

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
        if payload.get("type") == "after_sale_approval":
            print(f"      方案 {payload.get('plan_id')}  "
                  f"{payload.get('refund_type')} {payload.get('amount')} 元")
            # reason_trace 的条目自带 ✓/✗ 前缀，这里不再补一层
            for line in (payload.get("reason_trace") or []):
                print(f"      {line}")
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

class _scenario_env:
    """临时覆盖环境变量（场景级的故障注入用），退出时还原。

    端口实例是进程级单例、不会重建，但故障注入是在**每次调用时**读环境变量的
    （见 `integrations/mock/chaos.py`），所以 direct 模式下场景级覆盖自然生效。

    ⚠️ 但 MCP 模式下**不成立**：网关是一个常驻子进程，它的环境变量在
    进程启动那一刻就定格了，之后改父进程的 `os.environ` 完全送不进去。
    所以在进出场景时都调一次 `reset_ports()`：它会顺带关掉 MCP 会话，
    让下一次调用拿着**当前**的环境重新拉起网关。

    这一条不修的话，`TOOL_GATEWAY=mcp` 下「注入物流故障」的场景会静默地
    跑成正常路径 —— 而它看起来只是「这次降级没触发」，不会报任何错。
    """

    def __init__(self, overrides: dict):
        self.overrides = overrides or {}
        self.saved: dict = {}

    def __enter__(self):
        for name, value in self.overrides.items():
            self.saved[name] = os.environ.get(name)
            os.environ[name] = value
        if self.overrides:
            # 环境就位之后再重建端口：MCP 会话要按新环境重启
            self._rebuild_ports()
        return self

    def __exit__(self, *exc):
        for name, old in self.saved.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        if self.overrides:
            # 注入过的场景必须把网关也换掉：否则它带着上一次注入的环境活着，
            # 下一个场景会静默地继承那批故障
            self._rebuild_ports()
        return False

    @staticmethod
    def _rebuild_ports() -> None:
        from integrations.factory import reset_ports

        reset_ports()


def run_scenario(key: str, *, auto_yes: bool = False) -> int:
    """跑一个场景。返回退出码（0 成功）。"""
    from demo import DemoRunner, build_demo_graph

    spec = SCENARIOS[key]
    repeat = int(spec.get("repeat", 1))
    print(f"\n{_hr('═')}")
    print(f"  场景：{spec['title']}  [{key}]  "
          f"（TOOL_GATEWAY={os.environ.get('TOOL_GATEWAY', 'direct')}）")
    print(f"  输入：{spec['input']!r}")
    print(f"  说明：{spec['desc']}")
    if spec.get("env"):
        print(f"  注入：{spec['env']}")
    if repeat > 1:
        print(f"  重复：同一诉求提交 {repeat} 次（用来验证幂等短路）")
    print(_hr("═"))

    # 整个场景（含审批恢复）都跑在注入的环境里：中断-恢复之间故障不该消失，
    # 否则「审批通过后接口恢复了」会让降级分支看起来像是假的
    with _scenario_env(spec.get("env", {})):
        # 跑多次时共用同一个 runner/图：幂等记录写在 store 里，
        # 换一个图实例就换一个 store，短路也就无从谈起了
        runner = DemoRunner(build_demo_graph())

        for index in range(1, repeat + 1):
            if repeat > 1:
                print(f"\n  ── 第 {index} 次提交 ──")

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
    parser.add_argument(
        "--gateway",
        choices=("direct", "mcp"),
        help="工具调用路径：direct=进程内直调（默认），mcp=经 MCP 网关（需已装 mcp SDK）",
    )
    args = parser.parse_args(argv)

    # 在 import demo / 建图之前钉住：端口是进程级单例，第一次取值就定型了
    if args.gateway:
        os.environ["TOOL_GATEWAY"] = args.gateway

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

    gateway = os.environ.get("TOOL_GATEWAY", "direct")
    print(f"\n{_hr('═')}")
    print("  电商智能客服与运营助手 —— 离线演示")
    print(f"  模式：LLM_MODE={os.environ['LLM_MODE']}  "
          f"INTEGRATION_MODE={os.environ['INTEGRATION_MODE']}  "
          f"TOOL_GATEWAY={gateway}  （全程不联网）")
    if gateway == "mcp":
        print("  工具：经 MCP 网关（stdio 子进程）调用，与 direct 模式结果应当完全一致")
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
