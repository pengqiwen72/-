"""
LLM 层(可插拔)

设计:
- 配置了 LLM_API_KEY 时走在线模型(Qwen / OpenAI 兼容接口),用于语义意图识别、转述与摘要
- 未配置时自动降级为离线规则实现,保证 demo 在无 key 环境下也能完整跑通

设计要点:在线模型只参与语义意图识别、结果转述与摘要生成;事实性数据一律由业务接口提供,
模型不参与数据生成,数值计算全部由代码完成。
"""
import json
import os
import urllib.request

from app.models import Intent, LLMOutput, OrderInfo, WeeklyStats

# ---------------------------------------------------------------- 意图词表
# 高频业务意图:枚举精确匹配(零误判、不消耗模型调用)
INTENT_KEYWORDS = {
    Intent.ORDER_QUERY: ["订单", "物流", "快递", "发货", "到哪", "什么时候到", "签收"],
    Intent.AFTER_SALE: ["退款", "退货", "换货", "补发", "售后", "坏了", "破损", "少发", "质量"],
    Intent.WEEKLY_REPORT: ["周报", "本周", "环比", "数据汇总", "销售数据", "报表"],
    Intent.PRODUCT_QA: ["怎么用", "参数", "规格", "材质", "保质", "保修", "尺码", "活动", "优惠", "多少钱", "发货时间"],
}

# 意图优先级:多意图同时命中时,按业务风险从高到低裁决
# (售后 > 订单查询 > 周报 > 商品咨询)
INTENT_PRIORITY = [
    Intent.AFTER_SALE,
    Intent.ORDER_QUERY,
    Intent.WEEKLY_REPORT,
    Intent.PRODUCT_QA,
]


class LLMClient:
    """在线/离线双模 LLM 客户端"""

    def __init__(self) -> None:
        self.api_key = (
            os.getenv("LLM_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        self.base_url = os.getenv(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = os.getenv("LLM_MODEL", "qwen-plus")

    @property
    def online(self) -> bool:
        return bool(self.api_key)

    @property
    def mode(self) -> str:
        return f"online({self.model})" if self.online else "offline(规则实现)"

    # ------------------------------------------------------------ 底层调用
    def _chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    # ---------------------------------------------------- 1. 语义意图识别
    def semantic_intent(self, query: str) -> tuple[Intent, str]:
        """枚举未命中时的语义兜底识别"""
        if not self.online:
            return Intent.GENERAL, "枚举未命中 -> 离线模式兜底为通用问答"
        prompt = (
            "你是电商客服意图分类器。请判断用户问题属于哪一类,"
            f"可选值:{[i.value for i in Intent]}。\n"
            '只输出 JSON:{"intent":"<类别>","reason":"<10字内理由>"}'
        )
        try:
            raw = self._chat(prompt, f"用户问题:{query}")
            parsed = json.loads(raw)
            return Intent(parsed["intent"]), f"语义识别:{parsed.get('reason', '')}"
        except Exception as e:  # 网络/解析异常 -> 兜底
            return Intent.GENERAL, f"语义识别异常({e.__class__.__name__}) -> 通用问答兜底"

    # ------------------------------------------- 2. 基于检索上下文生成回答
    def answer_with_context(self, query: str, context: list[dict]) -> LLMOutput:
        if not self.online:
            best = context[0]
            return LLMOutput(
                answer=f"根据《{best['doc_name']}》:{best['content']}",
                confidence=best["score"],
            )
        ctx = "\n".join(f"[{i + 1}] {d['doc_name']}:{d['content']}" for i, d in enumerate(context))
        prompt = (
            "你是电商客服助手。只能依据给定资料回答,不得编造;资料未覆盖时回答'资料未覆盖'。\n"
            '只输出 JSON:{"answer":"<回答>","confidence":<0~1,有依据的高>}'
        )
        raw = self._chat(prompt, f"资料:\n{ctx}\n\n用户问题:{query}")
        return LLMOutput.safe_parse(raw)

    # ------------------------------------------ 3. 事实数据转述(不改数值)
    def paraphrase_order(self, order: OrderInfo) -> str:
        """事实性数据只做语言组织,数值/状态一律原样引用"""
        if not self.online:
            return (
                f"您的订单 {order.order_id}({order.product})当前状态为「{order.status}」,"
                f"物流信息:{order.logistics or '暂无'},订单金额 {order.amount} 元。"
            )
        prompt = (
            "你是电商客服。请把给定的订单数据转述为一句客服口吻的回复。"
            "严禁修改或新增任何数值、状态与时间,只做语言组织。"
            '只输出 JSON:{"answer":"<回复>","confidence":1.0}'
        )
        raw = self._chat(prompt, json.dumps(order.model_dump(), ensure_ascii=False))
        return LLMOutput.safe_parse(raw).answer or "订单查询完成,数据已由业务接口返回。"

    # --------------------------------------------------- 4. 周报文字摘要
    def summarize_weekly(self, stats: WeeklyStats) -> str:
        if not self.online:
            trend = "上升" if stats.gmv_wow >= 0 else "下降"
            abnormal = "、".join(stats.abnormal) if stats.abnormal else "无"
            return (
                f"本周 GMV {stats.gmv:.0f} 元,环比{trend} {abs(stats.gmv_wow):.1f}%;"
                f"订单量 {stats.orders} 单(环比 {stats.orders_wow:+.1f}%);"
                f"退款率 {stats.refund_rate:.2%}(环比 {stats.refund_rate_wow:+.1f}%)。"
                f"需关注:{abnormal}。"
            )
        prompt = (
            "你是电商运营分析师。基于给定的周报统计数据写 2~3 句中文摘要,"
            "数值必须与给定数据一致,不得推算或修改。\n"
            '只输出 JSON:{"answer":"<摘要>","confidence":1.0}'
        )
        raw = self._chat(prompt, json.dumps(stats.model_dump(), ensure_ascii=False))
        return LLMOutput.safe_parse(raw).answer or "周报数据已生成,请查看统计明细。"
