"""
守的是「意图路由三层」这条线（`src/graphs/cs_graph/cs_node.py`）。

    第一层  枚举精确匹配：task_input 就是菜单 id（如 `product_qa`）→ 零成本直达，**不调模型**
    第二层  语义识别：一句话交给 llm_intent_detect 判断
    第三层  兜底：模型给了枚举外的值，或模型直接抛异常 → 一律归 `no_clear_intention`

三层各自都有失效场景，单独看代码很难发现：第一层若被改成「先调模型再兜枚举」，
坐席点按钮就会白烧一次 token；第三层若没兜住，一个模型返回的野值会让图
直接走到不存在的节点上。
"""

from __future__ import annotations

import pytest

from graphs.cs_graph.cs_node import IntentDetection, intent_detection
from models.cs_models import Intent

# 语料/菜单里真实存在的意图 id（与 IntentDetection 枚举一致）
MENU_IDS = [
    IntentDetection.PRODUCT_QA.value,
    IntentDetection.ORDER_QUERY.value,
    IntentDetection.AFTER_SALE.value,
    IntentDetection.TAG_SUGGESTION.value,
]


def _route(task_input: str):
    """直接跑路由节点，拿到它给出的跳转目标（不需要整图）。"""
    command = intent_detection({"task_input": task_input}, {})
    return getattr(command, "goto", None)


def _forbid_model_calls(monkeypatch):
    """把语义层换成「一被调用就失败」，用来证明第一层真的没调模型。"""
    def _boom(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("枚举直达不该调用语义意图识别")

    monkeypatch.setattr("graphs.cs_graph.cs_node.llm_intent_detect", _boom)


# ==================== 第一层：枚举直达 ====================

@pytest.mark.parametrize("menu_id", MENU_IDS)
def test_menu_id_routes_directly_without_calling_the_model(monkeypatch, menu_id):
    """坐席点按钮传的就是菜单 id：直达对应子图，且**一次模型都不调**。"""
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
        ("我要退货退款", IntentDetection.AFTER_SALE.value),
        ("这款保温杯支持刻字吗", IntentDetection.PRODUCT_QA.value),
        ("帮我查一下订单 SO20260318002 到哪了", IntentDetection.ORDER_QUERY.value),
        ("给我出一份本周的运营数据周报", IntentDetection.WEEKLY_REPORT.value),
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
        "graphs.cs_graph.cs_node.llm_intent_detect",
        lambda *a, **k: Intent(intent_id="teleport_customer"),
    )
    assert _route("把客户传送到火星") == IntentDetection.NO_CLEAR_INTENTION.value


def test_semantic_layer_exception_falls_back_instead_of_crashing(monkeypatch):
    """语义识别抛异常（模型超时等）：兜底到通用问答，链路不能断。"""
    def _boom(*args, **kwargs):
        raise RuntimeError("模型调用超时")

    monkeypatch.setattr("graphs.cs_graph.cs_node.llm_intent_detect", _boom)
    assert _route("帮我处理点事情") == IntentDetection.NO_CLEAR_INTENTION.value


# ==================== 整图：路由结果真的落到了对应子图 ====================

def test_semantic_route_executes_only_the_matched_subgraph(runner):
    """路由到售后：状态里只有售后链路的产出，其余业务链路保持空。"""
    result = runner.start("订单 SO20260315001 的保温杯破损了，我要退款")
    assert result.error is None

    state = result.state
    assert state.get("after_sale") is not None
    assert state.get("product_qa") is None
    assert state.get("order_query") is None
    assert state.get("weekly_report") is None


def test_no_clear_intention_reaches_the_talk_subgraph_with_the_input(runner):
    """兜底链路：不进任何业务子图，且回答里带上用户原话（不是一句固定文案）。"""
    utterance = "你好呀"
    result = runner.start(utterance)
    assert result.error is None

    state = result.state
    assert state.get("product_qa") is None
    assert state.get("order_query") is None
    assert state.get("after_sale") is None

    message = result.task_result.task_result
    assert utterance in message, "通用问答要回应的是用户真说的那句话"
    assert "商品咨询" in message or "可以帮你" in message, "兜底要说明自身能力边界"
