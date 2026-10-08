"""
守的是「意图路由三层」这条线（`src/graphs/scm_graph/scm_node.py`）。

    第一层  枚举精确匹配：task_input 就是菜单 id（如 `purchase_request`）→ 零成本直达，**不调模型**
    第二层  语义识别：一句话交给 llm_intent_detect 判断（离线模式下是词表打分）
    第三层  兜底：模型给了枚举外的值，或模型直接抛异常 → 一律归 `no_clear_intention`

三层各自都有失效场景，单独看代码很难发现：第一层若被改成「先调模型再兜枚举」，
工作台点按钮就会白烧一次 token；第三层若没兜住，一个模型返回的野值会让图
直接走到不存在的节点上。

最后一段是**场景路由闸**：`demo/scenarios.py` 里每条演示输入，都必须真的落到
它想演示的那个子图。词表调一个词就可能让某个场景静默走错链路 —— 界面上不报错，
只是答非所问，所以用断言把 15 条场景的落点全部钉死。
"""

from __future__ import annotations

import pytest

from graphs.scm_graph.scm_node import IntentDetection, intent_detection
from models.scm_models import Intent

# 菜单里真实存在的意图 id（与 IntentDetection 枚举一致）
MENU_IDS = [
    IntentDetection.MATERIAL_QA.value,
    IntentDetection.PURCHASE_REQUEST.value,
    IntentDetection.GOODS_RECEIPT.value,
    IntentDetection.INVENTORY_QUERY.value,
    IntentDetection.SUPPLIER_QUERY.value,
    IntentDetection.NO_CLEAR_INTENTION.value,
]


def _route(task_input: str):
    """直接跑路由节点，拿到它给出的跳转目标（不需要整图）。"""
    command = intent_detection({"task_input": task_input}, {})
    return getattr(command, "goto", None)


def _forbid_model_calls(monkeypatch):
    """把语义层换成「一被调用就失败」，用来证明第一层真的没调模型。"""
    def _boom(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("枚举直达不该调用语义意图识别")

    monkeypatch.setattr("graphs.scm_graph.scm_node.llm_intent_detect", _boom)


# ==================== 第一层：枚举直达 ====================

@pytest.mark.parametrize("menu_id", MENU_IDS)
def test_menu_id_routes_directly_without_calling_the_model(monkeypatch, menu_id):
    """工作台点按钮传的就是菜单 id：直达对应子图，且**一次模型都不调**。"""
    _forbid_model_calls(monkeypatch)
    assert _route(menu_id) == menu_id


def test_every_intent_value_is_a_reachable_menu_id(monkeypatch):
    """枚举里的每个值都要能被第一层直达 —— 漏一个就是一个点了没反应的按钮。"""
    _forbid_model_calls(monkeypatch)
    for intent in IntentDetection:
        assert _route(intent.value) == intent.value


# ==================== 第二层：语义识别 ====================

@pytest.mark.parametrize(
    "utterance, expected",
    [
        # 采购申请
        ("给 WH01 申购 3000kg 冷轧钢板 1.2mm", IntentDetection.PURCHASE_REQUEST.value),
        ("帮我请购一批轴承，下周产线要用", IntentDetection.PURCHASE_REQUEST.value),
        # 收货验收
        ("PO20260928002 到货 920 支，开箱抽检发现 30 支磕碰变形",
         IntentDetection.GOODS_RECEIPT.value),
        # 库存
        ("M100023 各个仓库还有多少库存？", IntentDetection.INVENTORY_QUERY.value),
        ("M400301 库存还够吗？会不会断料？", IntentDetection.INVENTORY_QUERY.value),
        # 供应商
        ("M100023 哪家供应商报价便宜？", IntentDetection.SUPPLIER_QUERY.value),
        # 物料与制度问答：政策问题问的是「规定是什么」，不能被拽进采购执行
        ("采购申请超过多少金额需要主管审批？", IntentDetection.MATERIAL_QA.value),
        ("公司制度里有没有无人机送货的规定？", IntentDetection.MATERIAL_QA.value),
    ],
)
def test_natural_language_is_routed_by_semantics(utterance, expected):
    """自然语言不靠字面等值，而是走语义识别落到正确的业务链路。"""
    assert _route(utterance) == expected


# ==================== 第三层：兜底 ====================

def test_unrelated_sentence_falls_back_to_no_clear_intention():
    """一句与业务无关的话落到「未明确意图」，而不是碰巧排在第一位的某个链路。"""
    assert _route("今天天气怎么样呀") == IntentDetection.NO_CLEAR_INTENTION.value
    assert _route("你好呀") == IntentDetection.NO_CLEAR_INTENTION.value


def test_value_outside_the_enum_falls_back_instead_of_routing_into_the_void(monkeypatch):
    """模型返回了枚举外的值：必须收敛到兜底意图，而不是带着野值去跳节点。"""
    monkeypatch.setattr(
        "graphs.scm_graph.scm_node.llm_intent_detect",
        lambda *a, **k: Intent(intent_id="buy_the_company"),
    )
    assert _route("把整个仓库买下来") == IntentDetection.NO_CLEAR_INTENTION.value


def test_semantic_layer_exception_falls_back_instead_of_crashing(monkeypatch):
    """语义识别抛异常（模型超时等）：兜底到通用问答，链路不能断。"""
    def _boom(*args, **kwargs):
        raise RuntimeError("模型调用超时")

    monkeypatch.setattr("graphs.scm_graph.scm_node.llm_intent_detect", _boom)
    assert _route("帮我处理点事情") == IntentDetection.NO_CLEAR_INTENTION.value


# ==================== 整图：路由结果真的落到了对应子图 ====================

def test_semantic_route_executes_only_the_matched_subgraph(runner):
    """路由到库存：状态里只有库存链路的产出，其余业务链路保持空。"""
    result = runner.start("M100023 各个仓库还有多少库存？")
    assert result.error is None

    state = result.state
    assert state.get("inventory_query") is not None
    assert state.get("material_qa") is None
    assert state.get("purchase_request") is None
    assert state.get("goods_receipt") is None
    assert state.get("supplier_query") is None


def test_no_clear_intention_reaches_the_talk_subgraph_with_the_input(runner):
    """兜底链路：不进任何业务子图，且回答里带上员工原话（不是一句固定文案）。"""
    utterance = "你好呀"
    result = runner.start(utterance)
    assert result.error is None

    state = result.state
    assert state.get("material_qa") is None
    assert state.get("purchase_request") is None
    assert state.get("goods_receipt") is None
    assert state.get("inventory_query") is None
    assert state.get("supplier_query") is None

    message = result.task_result.task_result
    assert utterance in message, "通用问答要回应的是员工真说的那句话"
    assert "供应链" in message or "可以帮你" in message, "兜底要说明自身能力边界"


# ==================== 场景路由闸：15 条演示输入各归其位 ====================

#: 场景 key -> 它应当落到的子图名。演示场景的「输入」是自然语言，
#: 靠第二层语义路由分发 —— 词表一动就可能走错链路，所以在这里全部钉死。
SCENARIO_ROUTES = {
    "material_qa":          "material_qa",
    "material_qa_relation": "material_qa",     # 替代料问题同走问答子图（图谱通道在子图内分流）
    "qa_miss":              "material_qa",
    "qa_degraded":          "material_qa",
    "inventory_query":      "inventory_query",
    "inventory_low":        "inventory_query",
    "supplier_compare":     "supplier_query",
    "supplier_score":       "supplier_query",
    "purchase_auto":        "purchase_request",
    "purchase_approval":    "purchase_request",
    "purchase_reject":      "purchase_request",
    "purchase_replay":      "purchase_request",
    "receipt_normal":       "goods_receipt",
    "receipt_quality":      "goods_receipt",
    "talk":                 "no_clear_intention",
}


def test_every_demo_scenario_routes_to_the_subgraph_it_advertises():
    from demo.scenarios import SCENARIOS

    assert set(SCENARIOS) == set(SCENARIO_ROUTES), (
        "场景表加了/删了条目，这条闸也要同步 —— 否则新场景没人守"
    )

    mismatched = {
        key: (spec["input"], _route(spec["input"]), SCENARIO_ROUTES[key])
        for key, spec in SCENARIOS.items()
        if _route(spec["input"]) != SCENARIO_ROUTES[key]
    }
    assert not mismatched, f"场景被路由到了别的子图（输入, 实际, 期望）：{mismatched}"
