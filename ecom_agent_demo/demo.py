"""
Demo 入口:演示电商智能客服与运营助手的 5 个核心场景

运行:python demo.py
(未配置 LLM_API_KEY 时自动使用离线规则实现,全流程仍可跑通)
"""
import sys

from langgraph.types import Command

from app.graph import graph, llm
from app.tools import idempotency_store

# Windows 控制台中文输出防护(避免 GBK 编码乱码)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SESSION = "demo-session-001"
USER = "user-1001"


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _print_result(result: dict, title: str) -> None:
    tr = result.get("task_result")
    print(f"\n{'=' * 78}\n【{title}】")
    print(f"意图路由 : {result.get('intent')}  |  {result.get('route_reason')}")
    if tr:
        print(f"处理状态 : {tr.status}")
        print(f"回复内容 : {tr.content}")
        if tr.sources:
            print("知识出处 :")
            for s in tr.sources:
                print(f"           - 《{s.doc_name}》 相似度 {s.score}")
    print("执行记录 :")
    for n in result.get("node_result", []) or []:
        print(f"           [{n.node}] {n.detail}")


def run_scene(title: str, query: str, session_id: str = SESSION) -> dict:
    """跑一个场景(单轮,无人工介入)"""
    result = graph.invoke(
        {"query": query, "user_id": USER, "session_id": session_id},
        _config(session_id),
    )
    _print_result(result, title)
    return result


def main() -> None:
    print(f"LLM 模式:{llm.mode}")

    # 场景 1:商品咨询 -> RAG 命中,回答带出处
    run_scene("场景 1:商品咨询(RAG 命中,回答带出处)", "智能保温杯的容量和材质是什么?")

    # 场景 2:知识库未覆盖 -> 低置信度转人工兜底
    run_scene("场景 2:知识库未覆盖(置信度不足 -> 转人工兜底)", "你们支持以旧换新吗?政策是怎么样的?")

    # 场景 3:订单查询 -> 多 API 编排 + 模型仅转述
    run_scene("场景 3:订单查询(多 API 编排 + 模型仅转述)", "帮我查下订单 SO20260618001 到哪了?")

    # 场景 4(第一部分):高风险售后 -> interrupt 挂起等待人工确认
    print(f"\n{'=' * 78}\n【场景 4:高风险售后 -> HITL 人工确认】")
    result = graph.invoke(
        {"query": "订单 SO20260620007 我要退款", "user_id": USER, "session_id": SESSION},
        _config(SESSION),
    )
    interrupts = result.get("__interrupt__") or []
    if not interrupts:
        print("⚠ 未触发人工确认,请检查路由与风控阈值")
        sys.exit(1)
    payload = interrupts[0].value
    print("⏸  工作流已挂起(interrupt),等待人工审批:")
    for k in ("order_id", "action", "amount", "risk_level", "reason", "question"):
        print(f"           {k}: {payload[k]}")
    print(f"           (挂起期间 State 由 checkpointer 持久化,线程 {SESSION})")

    # 场景 4(第二部分):主管审批通过 -> resume 恢复执行
    print("\n▶  主管审批:approve  ->  Command(resume='approve') 恢复执行")
    result = graph.invoke(Command(resume="approve"), _config(SESSION))
    _print_result(result, "场景 4:审批通过后执行结果")

    # 场景 5:幂等验证 -> 同一操作重复提交
    print(f"\n{'=' * 78}\n【场景 5:幂等控制验证】")
    print("模拟同一笔退款被重复提交两次(网络重试 / 客服重复点击):")
    for i in (1, 2):
        r = idempotency_store.execute("SO20260618001", "refund", 199.0)
        tag = "第二次提交 -> 幂等命中,直接返回首次结果" if r["idempotent_replay"] else "首次提交 -> 真实执行"
        print(f"           第 {i} 次:{tag};工单号 {r['ticket']}")
    print(f"           幂等命中次数:{idempotency_store.hits}(同一次售后操作仅产生一个工单,未重复退款)")
    print(f"           注:场景 4 的 1280 元退款同样以「订单号:refund」为幂等键,重放同一请求不会重复执行")

    # 场景 6:数据周报 -> 代码计算 + 模型摘要
    run_scene("场景 6:数据周报(代码计算数值 + 模型生成摘要)", "帮我出一下本周的数据周报")

    print(f"\n{'=' * 78}\nDemo 执行结束(共 6 个场景)\n")


if __name__ == "__main__":
    main()
