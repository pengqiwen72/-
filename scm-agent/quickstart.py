#!/usr/bin/env python
"""
零配置演示入口 —— 不启动任何服务，直接跑通智能体全链路。

    python quickstart.py                    # 交互模式：跑一遍，遇到审批停下来问你
    python quickstart.py --yes              # 非交互：审批按场景预置决定，缺省自动通过（CI 用）
    python quickstart.py --list             # 列出所有场景
    python quickstart.py -s receipt_quality # 只跑指定场景

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
# 按**脚本自身的位置**定位，不依赖 cwd —— 从仓库根、从 scm_agent、从任意目录
# 调用都成立（`python /path/to/scm-agent/quickstart.py` 也行）。
_ROOT = Path(__file__).resolve().parent          # 仓库根
_SRC = _ROOT / "scm_agent" / "src"
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

# 场景表长在 `demo/scenarios.py`：命令行与坐席工作台的 `/scm/samples` 共用同一份，
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
    """打印本次运行的业务产出。

    五个业务字段与菜单 id 一一对应 —— 「哪个字段非空」就等于「这次走了哪条链路」，
    这条对应关系既方便排障，也是这套统一状态模型的价值所在。
    """
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

    material_qa = state.get("material_qa")
    if material_qa is not None:
        print(f"\n  问答：通道 {material_qa.channel}  置信度 {material_qa.confidence:.2f}  "
              f"状态 {material_qa.status}  出处 {len(material_qa.citations)} 条"
              f"{'  ⚠ 已降级' if material_qa.degraded else ''}")
        if material_qa.matched_terms:
            print(f"  命中词：{'、'.join(material_qa.matched_terms)}")
        if material_qa.reason:
            print(f"  转人工原因：{material_qa.reason}")

    purchase = state.get("purchase_request")
    if purchase is not None:
        print(f"\n  采购方案：{purchase.biz_id or '—'}  策略 {purchase.policy_id or '—'}"
              f"（{purchase.policy_title or '—'}）")
        print(f"  物料：{purchase.material_name or '—'}（{purchase.material_code or '—'}）"
              f"  {purchase.qty:g}{purchase.unit or ''}  金额 {purchase.amount:.2f} 元  "
              f"状态 {purchase.status}"
              f"{'  审批人 ' + purchase.approved_by if purchase.approved_by else ''}")
        if purchase.po_no:
            print(f"  采购订单：{purchase.po_no}"
                  f"{'（命中幂等，未重复下单）' if purchase.replayed else ''}")
        if purchase.reason_trace:
            # 标记已由节点写进字符串，这里原样打印 —— 见 sub_purchase_request_node
            print("  判定依据：")
            for line in purchase.reason_trace:
                print(f"    {line}")

    receipt = state.get("goods_receipt")
    if receipt is not None:
        print(f"\n  收货方案：{receipt.biz_id or '—'}  策略 {receipt.policy_id or '—'}"
              f"（{receipt.policy_title or '—'}）")
        print(f"  实收 {receipt.qty_received}  不良 {receipt.qty_defect}  "
              f"不良率 {receipt.defect_rate:.2%}  处置 {receipt.disposition or '—'}  "
              f"状态 {receipt.status}"
              f"{'  复核人 ' + receipt.approved_by if receipt.approved_by else ''}")
        if receipt.reason_trace:
            print("  判定依据：")
            for line in receipt.reason_trace:
                print(f"    {line}")

    inventory = state.get("inventory_query")
    if inventory is not None:
        print(f"\n  库存报告：{inventory.material_name}（{inventory.material_code}）"
              f"  可用合计 {inventory.total_available}{inventory.unit}"
              f"{'  ⚠ 存在低于安全线的仓' if inventory.below_safety else ''}")
        for row in inventory.rows:
            print(f"    {row.render()}")
        for transfer in inventory.transfers:
            print(f"    {transfer.render()}")
        if inventory.summary:
            print(f"  结论：{inventory.summary}")

    supplier = state.get("supplier_query")
    if supplier is not None and hasattr(supplier, "scores"):
        # 比价排名（定位到物料）
        print(f"\n  比价结果：{supplier.material_name}（{supplier.material_code}）"
              f"  最优 {supplier.best_supplier_code or '—'}")
        for score in supplier.scores:
            print(f"    {score.render()}")
        if supplier.summary:
            print(f"  结论：{supplier.summary}")
    elif supplier is not None and hasattr(supplier, "lead_time_days"):
        # 履约评估（定位到供应商）—— 两个模型共用 supplier_query 字段
        print(f"\n  履约评估：{supplier.supplier_name}（{supplier.supplier_code}）"
              f"  交期 {supplier.lead_time_days} 天"
              f"  准时率 {supplier.on_time_rate:.0%}"
              f"  不良率 {supplier.defect_rate:.2%}"
              f"{'  新供应商' if supplier.is_new else ''}")
        if supplier.lines:
            print(f"  价格竞争力：{supplier.quoted_count} 个在报物料，"
                  f"{supplier.lowest_count} 个最低报价，平均价格分 {supplier.avg_price_score:.3f}")
            print(f"  综合履约评分 {supplier.score:.3f}")
        if supplier.note:
            print(f"  说明：{supplier.note}")
        if supplier.summary:
            print(f"  结论：{supplier.summary}")


def _ask_approval(interrupts: list, auto_yes: bool, scripted: list | None = None) -> str:
    """处理人工审批。返回回给图的决策值。

    `scripted` 是场景预置的决定队列（`spec["decisions"]`）：有些场景的看点
    就在「驳回」「复核不通过」这类分支上（见 `demo/scenarios.py` 的
    `purchase_reject` / `receipt_quality`），靠人手现场点选演不稳定，
    所以预置决定优先于一切交互；取完之后才回落 —— 非交互模式默认通过，
    交互模式回到人工点选。
    """
    payload = interrupts[0].value if interrupts else {}
    if isinstance(payload, dict):
        desc = payload.get("description", "需要你确认")
        print(f"\n  ⏸  等待人工审批：{desc}")
        kind = payload.get("type")
        if kind == "purchase_approval":
            print(f"      方案 {payload.get('plan_id')}  申请单 {payload.get('biz_id')}")
            print(f"      {payload.get('material_name')}（{payload.get('material_code')}）"
                  f"  {payload.get('qty')}{payload.get('unit')} × "
                  f"{payload.get('unit_price')} 元 = {payload.get('amount')} 元")
            print(f"      到货仓 {payload.get('target_warehouse')}  "
                  f"预计到货 {payload.get('expect_arrival')}")
        elif kind == "quality_review":
            print(f"      方案 {payload.get('plan_id')}  采购订单 {payload.get('po_no')}")
            print(f"      {payload.get('material_name')}（{payload.get('material_code')}）"
                  f"  实收 {payload.get('qty_received')}  不良 {payload.get('qty_defect')}  "
                  f"不良率 {float(payload.get('defect_rate') or 0):.2%}")
            if payload.get("defect_type"):
                print(f"      不良类型：{payload['defect_type']}")
        # reason_trace 的条目自带 ✓/✗ 前缀，这里不再补一层
        for line in (payload.get("reason_trace") or []):
            print(f"      {line}")
    else:
        print(f"\n  ⏸  等待人工审批：{payload}")

    if scripted:
        decision = scripted.pop(0)
        print(f"  → 按场景预置决定：{decision}")
        return decision

    if auto_yes:
        print("  → 非交互模式，自动确认（ok）")
        return "ok"

    print("\n  请选择：[1] 确认  [2] 放弃")
    try:
        choice = input("  > ").strip()
    except EOFError:  # pragma: no cover - 无 stdin 时
        print("  （读不到输入，视为确认）")
        return "ok"

    return {"1": "ok", "2": "discard"}.get(choice, "ok")


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
    from store.store_provider import reset_fallback_store

    spec = SCENARIOS[key]
    repeat = int(spec.get("repeat", 1))
    # 预置决定的队列按「每个场景一份新的」来算：重复提交的场景不能第一轮就把
    # 决定用光。列表短了取完即止 —— 后续中断回落到交互/默认。
    scripted = list(spec.get("decisions") or [])
    print(f"\n{_hr('═')}")
    print(f"  场景：{spec['title']}  [{key}]  "
          f"（TOOL_GATEWAY={os.environ.get('TOOL_GATEWAY', 'direct')}）")
    print(f"  输入：{spec['input']!r}")
    print(f"  说明：{spec['desc']}")
    if spec.get("env"):
        print(f"  注入：{spec['env']}")
    if scripted:
        print(f"  预置审批决定：{scripted}")
    if repeat > 1:
        print(f"  重复：同一诉求提交 {repeat} 次（用来验证幂等短路）")
    print(_hr("═"))

    # 整个场景（含审批恢复）都跑在注入的环境里：中断-恢复之间故障不该消失，
    # 否则「审批通过后接口恢复了」会让降级分支看起来像是假的
    with _scenario_env(spec.get("env", {})):
        # 每个场景从一块干净的白板开始。幂等记录、审批记录都写在**进程级**
        # 的兜底 store 里，而几个采购场景刻意用同一笔申请演示不同走向
        # （批准 / 驳回 / 改判重报 —— 单号相同是设计，不是巧合）。不重置的话，
        # 前一个场景真正下过的单会让后一个场景撞上「同号不同要素」的幂等冲突、
        # 落进「转人工核实」——那是系统面对真实冲突时的正确行为，但不是这个
        # 场景要讲的故事。
        reset_fallback_store()

        # 跑多次时共用同一个 runner/图：两次提交落在同一块 store 上，
        # 幂等短路才有机会被观察到
        runner = DemoRunner(build_demo_graph())

        for index in range(1, repeat + 1):
            if repeat > 1:
                print(f"\n  ── 第 {index} 次提交 ──")

            result = runner.start(spec["input"])

            print("\n  执行过程：")
            _print_progress(result)

            # 最多处理 3 轮审批，避免异常情况下无限循环
            rounds = 0
            while result.interrupted and rounds < 3:
                rounds += 1
                decision = _ask_approval(result.interrupts, auto_yes, scripted)
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
        description="供应链采购仓储智能助手 —— 离线零配置演示",
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
            print(f"  {key:<22} {spec['title']:<24} {spec['desc']}")
        print()
        return 0

    keys = args.scenario or DEFAULT_ORDER
    unknown = [k for k in keys if k not in SCENARIOS]
    if unknown:
        print(f"未知场景：{', '.join(unknown)}（用 --list 查看可用场景）", file=sys.stderr)
        return 2

    gateway = os.environ.get("TOOL_GATEWAY", "direct")
    print(f"\n{_hr('═')}")
    print("  供应链采购仓储智能助手 —— 离线演示")
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
