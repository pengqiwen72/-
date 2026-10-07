"""
演示场景表 —— 「这个 demo 能演什么」的唯一定义处。

为什么单独成一个模块
-------------------
这张表有两个消费者：命令行入口 `quickstart.py` 和坐席工作台的 `/scm/samples`
接口。它原先长在 quickstart.py 里，于是 webapp 只能 `from quickstart import
SCENARIOS` —— 依赖当前工作目录在 `sys.path` 上才算得通。而 `uvicorn` 这类
控制台脚本的 `sys.path[0]` 是 `.venv/Scripts` 而不是 cwd，接口就会静默返回
空清单（页面正常打开，只是示例按钮一个都没有）。

挪进 `src/demo/` 之后两边都从 `demo.scenarios` 取，既没有 cwd 依赖，
也去掉了 webapp → CLI 脚本这层倒置的依赖。

字段约定
--------
    title   标题，列表与页面上显示的名字
    input   喂给图的 `task_input`（原文照发，不要在这里改写客户措辞）
    desc    一句话说明这条链路在验证什么
    env     可选，场景级环境变量覆盖（故障注入用）
    repeat  可选，同一诉求提交几次（幂等演示用）
"""

from __future__ import annotations

SCENARIOS: dict[str, dict] = {
    "qa": {
        "title": "商品咨询问答",
        "input": "product_qa",
        "desc": "意图枚举直达商品咨询子图，问题取自客户最后一条消息；"
                "回答带出处页脚，置信度不足则转人工。",
    },
    "qa_miss": {
        "title": "商品咨询（知识库未覆盖）",
        "input": "这款保温杯支持无线充电吗？",
        "desc": "走语义意图识别进商品咨询子图；资料里没有「无线充电」，"
                "诉求词无着落 → 转人工，而不是硬编一个答案。",
    },
    "order": {
        "title": "订单联合查询",
        "input": "帮我查一下订单 SO20260318002 到哪了",
        "desc": "订单/物流/库存三路接口并行调用，逐条打印字段归一映射"
                "（orderNo→order_id、分→元、毫秒时间戳→日期、嵌套地址扁平化）。",
    },
    "order_degraded": {
        "title": "订单查询（物流接口故障）",
        "input": "帮我查一下订单 SO20260318002 到哪了",
        "desc": "注入物流接口故障，验证「部分失败不整体失败」："
                "订单与库存照常返回，缺的那一路记进 degraded 并如实告知客户。",
        "env": {"MOCK_FAIL_ACTIONS": "logistics"},
    },
    "aftersale": {
        "title": "售后处理（低金额自动退款）",
        "input": "订单 SO20260315001 的保温杯杯盖密封圈破损了，我要退款",
        "desc": "政策引擎按「全条件 AND + 优先级」匹配规则库，命中破损包赔；"
                "金额低于审批线直接执行，并打印每条条件的判定依据。",
    },
    "aftersale_replay": {
        "title": "售后重复提交（幂等）",
        "input": "订单 SO20260315001 的保温杯杯盖密封圈破损了，我要退款",
        "desc": "同一诉求提交两次：第二次命中幂等记录后短路返回首次结果，"
                "不会产生第二个退款单号。",
        "repeat": 2,
    },
    "aftersale_approval": {
        "title": "售后处理（高金额人工审批）",
        "input": "订单 SO20260320008 的落地灯灯臂磕碰了，我要退货退款",
        "desc": "599 元越过审批线 → interrupt 等人工作出决定 → 批准后由独立的执行节点"
                "发起退款；中断节点本身无副作用，所以重放不会重复退款。",
    },
    "report": {
        "title": "运营数据周报",
        "input": "给我出一份本周的运营数据周报",
        "desc": "取数 → 算数 → 说话 三段分离：指标与同环比由确定性代码算，"
                "模型只写摘要，且摘要里出现的每个数字都必须能在指标表里找到。",
    },
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

#: 默认执行顺序 —— 由浅入深：先看路由，再看取数与审批，最后是带人工确认的几条子图。
#: 「闲聊」放最前面是因为它最不依赖种子数据，链路最短，先跑通能立刻确认环境没问题。
DEFAULT_ORDER = [
    "talk", "qa", "qa_miss",
    "order", "order_degraded",
    "aftersale", "aftersale_replay", "aftersale_approval",
    "report",
    "chat", "tag", "schedule", "profile",
]


#: 不适合直接摆成页面按钮的场景，以及原因：
#:   * order_degraded    —— 点了就往业务接口里注入故障，工作台上不该有这种按钮
#:   * aftersale_replay  —— 靠「提交两次」才看得出效果，只点一次看不到幂等短路
#:   * qa                —— input 是意图枚举名而非一句人话，问题取自客户最后一条消息
_UI_EXCLUDED = frozenset({"order_degraded", "aftersale_replay", "qa"})


def ui_samples() -> list[dict]:
    """工作台页面用的「一键提问」清单。

    与命令行共用同一张表，所以标题、措辞、说明文字只有这一处，
    不会出现「CLI 里能跑、页面上点了报错」的错位。

    两重过滤：显式排除上面那三个；再挡掉带 `env` / `repeat` 的场景 ——
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
