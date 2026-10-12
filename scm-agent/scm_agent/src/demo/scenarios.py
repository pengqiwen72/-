"""
演示场景表 —— 「这个 demo 能演什么」的唯一定义处。

为什么单独成一个模块
-------------------
这张表有两个消费者：命令行入口 `quickstart.py` 和演示工作台的 `/scm/samples`
接口。它原先长在 quickstart.py 里，于是 webapp 只能 `from quickstart import
SCENARIOS` —— 依赖当前工作目录在 `sys.path` 上才算得通。而 `uvicorn` 这类
控制台脚本的 `sys.path[0]` 是 `.venv/Scripts` 而不是 cwd，接口就会静默返回
空清单（页面正常打开，只是示例按钮一个都没有）。

挪进 `src/demo/` 之后两边都从 `demo.scenarios` 取，既没有 cwd 依赖，
也去掉了 webapp → CLI 脚本这层倒置的依赖。

字段约定
--------
    title   标题，列表与页面上显示的名字
    input   喂给图的 `task_input`（原文照发，不要在这里改写用户措辞）
    desc    一句话说明这条链路在验证什么
    env     可选，场景级环境变量覆盖（故障注入用）
    repeat  可选，同一诉求提交几次（幂等演示用）
    decisions  可选，脚本化的人工决定（`-y` 时依次取用，用尽回落交互）
"""

from __future__ import annotations

SCENARIOS: dict[str, dict] = {
    # ---- 智能问答（RAG 文档通道）----
    "material_qa": {
        "title": "物料与制度问答（制度类）",
        "input": "采购申请超过多少金额需要主管审批？",
        "desc": "意图枚举直达问答子图，双通道检索制度文档；"
                "回答带出处页脚，置信度不足则转人工。",
    },
    "material_qa_relation": {
        "title": "物料问答（替代料，走图谱通道）",
        "input": "M100023 断料了，有没有可以替代的物料？有货吗？",
        "desc": "关系类问题走图谱多跳：物料 → 替代料 → 有货仓，"
                "回答里的数量全部来自图节点属性，天然可溯源；"
                "制度条款与图事实各占一条引用（两类证据量纲不同，"
                "引用清单按配额各留一位，见 rag/index.py 的说明）。",
    },
    "qa_miss": {
        "title": "问答（知识库未覆盖）",
        "input": "公司制度里有没有无人机送货的规定？",
        "desc": "走语义意图识别进问答子图；语料里没有对应条款，"
                "诉求词无着落 → 转人工，而不是硬编一个答案。",
    },
    "qa_degraded": {
        "title": "问答（图谱通道故障降级）",
        "input": "M100023 断料了，有没有可以替代的物料？有货吗？",
        "desc": "注入图谱接口故障并连跑 4 次：前三次失败后熔断器打开，"
                "第四次 L1 快速失败 —— 降级为纯文档通道并在页脚注明，"
                "链路不整体失败。",
        "env": {"MOCK_FAIL_ACTIONS": "graph"},
        "repeat": 4,
    },
    # ---- 韧性（装载链路故障熔断）----
    "load_degraded": {
        "title": "装载链路故障熔断（物料一路挂）",
        "input": "M100023 各个仓库还有多少库存？",
        "desc": "给物料主数据这一路注入故障并连跑 4 次：前三次该域装载失败"
                "（记 code=1，其余数据域与链路整体照常），第四次熔断器打开、"
                "快速失败 —— 「部分失败不整体失败」与「坏依赖不再等超时」"
                "在装载链路上同样成立。",
        "env": {"MOCK_FAIL_ACTIONS": "material"},
        "repeat": 4,
    },
    # ---- 库存管理 ----
    "inventory_query": {
        "title": "库存查询（多仓分布）",
        "input": "M100023 各个仓库还有多少库存？",
        "desc": "按仓库展开库存快照：可用量 = 在库 − 预留，"
                "逐仓判定安全库存并给出可支撑天数。",
    },
    "inventory_low": {
        "title": "安全库存预警（含调拨建议）",
        "input": "M400301 库存还够吗？会不会断料？",
        "desc": "WH02 低于安全线 → 报预警而不是只报数字，并按「余量仓 → 缺口仓」"
                "给出调拨建议（WH01 调 100 支到 WH02，调出后仍不低于安全线）；"
                "同一条建议在图谱通道也有一条，测试逐项比对两条路径。",
    },
    # ---- 供应商管理 ----
    "supplier_compare": {
        "title": "供应商比价",
        "input": "M100023 哪家供应商报价便宜？",
        "desc": "取三家报价按「价格 / 准时率 / 不良率」加权评分排名，"
                "并给出最高与最低报价的价差。",
    },
    "supplier_score": {
        "title": "供应商履约评估",
        "input": "帮我看看 S007 这家供应商的履约情况，交期和不良率怎么样",
        "desc": "按供应商编码（或名称）定位，给出承诺交期、准时交付率、不良率、"
                "跨物料价格竞争力与综合履约评分——与比价同一套加权口径，"
                "只读计算不落库，同一种子连查两次结果逐字段一致。",
    },
    # ---- 采购执行（HITL）----
    "purchase_auto": {
        "title": "采购申请（免审批自动下单）",
        "input": "给 WH01 申购 3000kg 冷轧钢板 1.2mm，指定 S001",
        "desc": "要素提取 → 按 S001 报价取价（5.80 元/公斤，同料均价 5.51）→ "
                "数量在直采范围内 → 免审批直接下单；申请单号由要素确定性生成。",
    },
    "purchase_approval": {
        "title": "采购申请（新供应商转人工审批）",
        "input": "给 WH01 申购 8000kg 冷轧钢板 1.2mm，指定 S012，产线急用",
        "desc": "8000kg×5.60=44800 元命中「新供应商首单需审批」→ interrupt 等主管"
                "决定 → 批准后由独立的执行节点下单；中断节点无副作用，所以重放不会重复下单。",
    },
    "purchase_reject": {
        "title": "采购申请（审批驳回）",
        "input": "给 WH01 申购 8000kg 冷轧钢板 1.2mm，指定 S012，产线急用",
        "desc": "同一条新供应商申请，这次审批给出驳回：方案标记 rejected，"
                "执行节点不做任何写操作，如实告知申请人。",
        "decisions": ["discard"],
    },
    "purchase_recreate": {
        "title": "采购申请（改判重报，免审批下单）",
        "input": "给 WH01 申购 8000kg 冷轧钢板 1.2mm，指定 S012",
        "desc": "审批不通过不是终点：用 recreate 把供应商改为 S001 重报 → 策略复判"
                "不再命中审批条款 → 免审批直接下单。「驳回带修改重报」而不是"
                "「从头再提一遍」，就是一次通过率指标背后的那条路。",
        "decisions": [{"decision": "recreate", "patch": {"supplier_code": "S001"}}],
    },
    "purchase_replay": {
        "title": "采购申请重复提交（幂等）",
        "input": "给 WH01 申购 3000kg 冷轧钢板 1.2mm，指定 S001",
        "desc": "同一诉求提交两次：第二次命中幂等记录后短路返回首次结果，"
                "不会产生第二张采购订单。",
        "repeat": 2,
    },
    # ---- 收货验收（HITL）----
    "receipt_normal": {
        "title": "收货验收（正常入库）",
        "input": "PO20260928002 到货了，实收 1000 支，没有发现问题",
        "desc": "不良率 0% 未命中 QC-001 → 直接办理入库，不惊动人工。",
    },
    "receipt_quality": {
        "title": "收货验收（不良率超阈值复核）",
        "input": "PO20260928002 到货 920 支，开箱抽检发现 30 支磕碰变形",
        "desc": "不良率 3.26% 命中 QC-001 → interrupt 等质检复核 → 复核人改判重报"
                "（处置改为让步接收）→ 不良率是事实改不了、复判仍要复核 → "
                "第二轮确认后按改后的处置执行。",
        "decisions": [
            {"decision": "recreate", "patch": {"disposition": "让步接收"}},
            "ok",
        ],
    },
    # ---- 兜底 ----
    "talk": {
        "title": "闲聊兜底",
        "input": "你好呀",
        "desc": "没有明确意图时走通用问答，说明自身能力边界。",
    },
}

#: 默认执行顺序 —— 由浅入深：先看路由与只读查询，再看写链路与人工确认。
#: 「闲聊」放最前面是因为它最不依赖种子数据，链路最短，先跑通能立刻确认环境没问题。
DEFAULT_ORDER = [
    "talk",
    "material_qa", "material_qa_relation", "qa_miss", "qa_degraded", "load_degraded",
    "inventory_query", "inventory_low",
    "supplier_compare", "supplier_score",
    "purchase_auto", "purchase_approval", "purchase_reject", "purchase_recreate",
    "purchase_replay",
    "receipt_normal", "receipt_quality",
]


#: 不适合直接摆成页面按钮的场景，以及原因：
#:   * qa_degraded       —— 点了就往图谱接口里注入故障并连跑 4 次，工作台上不该有这种按钮
#:   * purchase_replay   —— 靠「提交两次」才看得出效果，只点一次看不到幂等短路
#:   * purchase_reject   —— decisions 预置了「驳回」，按钮上跑不出「换个人来批」的体验
#:   * purchase_recreate —— decisions 预置了「改判重报」，页面上应当由人现场点
#:   * receipt_quality   —— decisions 预置了复核结论，页面上应当由人现场点
_UI_EXCLUDED = frozenset({
    "qa_degraded", "purchase_replay", "purchase_reject", "purchase_recreate",
    "receipt_quality",
})


def ui_samples() -> list[dict]:
    """工作台页面用的「一键提问」清单。

    与命令行共用同一张表，所以标题、措辞、说明文字只有这一处，
    不会出现「CLI 里能跑、页面上点了报错」的错位。

    两重过滤：显式排除上面那几个；再挡掉带 `env` / `repeat` 的场景 ——
    将来有人加了新的故障注入场景，它会自动留在命令行里，不会被顺手带上页面。
    """
    return [
        {
            "key": key,
            "title": spec.get("title", key),
            "input": spec.get("input", ""),
            "desc": spec.get("desc", ""),
        }
        for key, spec in SCENARIOS.items()
        if key not in _UI_EXCLUDED and not spec.get("env") and not spec.get("repeat")
    ]
