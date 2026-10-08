"""
守的是「要素提取拿到的是真实提示词」这条线（`llm/llm_{purchase,receipt}_draft.py`
× `llm/offline/synth_scm.py`）。

这一层在 test_seed_scm 之外单独存在的理由
-----------------------------------------
`test_seed_scm` 用**手搭的提示词骨架**练合成器的抽取规则；这里练另一半 ——
真实模板（`build_purchase_prompt` / `build_receipt_prompt`）产出的提示词，
合成器反解析得动。分开是有意的：模板改坏时，手搭骨架的用例照样全绿，
只有这一层会红。

两个模板各有一条「静默失效」通道，都不抛异常、只是字段悄悄变空，
然后被上层的澄清分支接走 —— 现场表现是「这助手怎么连这个都听不出来」：

  * 采购：候选物料要是没以 `{"materials": [...]}` 顶层块嵌入，名称匹配就退化成
    只剩编码直命中，主数据全在提示词里也照样抽不出物料；
  * 收货：PO 要是被包成一个大块，`po_no` 沉进嵌套层，按单号反查全部落空，
    物料编码变成空值。

所以这里直接在**真实提示词**上断言结构契约与端到端抽取结果。
"""

from __future__ import annotations

import json

import pytest

from llm.offline.prompt_view import PromptView
from llm.offline.registry import build_payload
from models.scm_models import PurchaseDraft, ReceiptDraft


@pytest.fixture
def master_data():
    """灌好种子的 store 里取主数据 —— 与两个子图装载进状态的是同一份。"""
    from store.seed_loader import load_seed_data
    from store.store_provider import get_fallback_store

    load_seed_data(get_fallback_store())

    from tools import store_tool as st

    return st.get_materials(), st.get_warehouses(), st.get_purchase_orders()


# ==================== 采购申请要素提取 ====================

def test_purchase_draft_extracts_elements_from_the_real_prompt(master_data):
    """一句典型申请，走真实提示词：编码/数量/仓库都从候选块与原文里来。"""
    from llm.llm_purchase_draft import llm_purchase_draft

    materials, warehouses, _ = master_data
    utterance = "给 WH01 补 3000kg 冷轧钢板 1.2mm"

    draft = llm_purchase_draft(utterance, materials, warehouses)

    # 种子里有「冷轧钢板 1.2mm」和「冷轧钢板 1.5mm」两个相近的物料，
    # 规格段「1.2mm」必须把 1.5mm 那个排除掉（名称切段匹配的存在意义）
    assert draft.material_code == "M100023"
    assert draft.material_query == "冷轧钢板 1.2mm"
    assert (draft.qty, draft.unit) == (3000.0, "kg")
    assert draft.target_warehouse == "WH01"
    assert utterance in draft.reason, "提报说明要原样保留，审批人看的就是它"


def test_purchase_draft_does_not_guess_with_the_real_candidate_set(master_data):
    """抽不到就不猜：一句模糊申请在全量主数据里也匹配不到任何物料。"""
    from llm.llm_purchase_draft import llm_purchase_draft

    materials, warehouses, _ = master_data
    draft = llm_purchase_draft("随便买点啥吧", materials, warehouses)

    assert (draft.material_code, draft.qty, draft.target_warehouse) == ("", 0.0, "")


def test_real_prompts_keep_the_blocks_the_synthesizer_reverses(master_data):
    """真实模板的结构契约 —— 合成器反解析所依赖的三个前提。

    这些前提写在 `llm_purchase_draft.py` 的模块文档里，改模板的人未必会读到；
    用断言替文档站岗：候选块的顶层键、最后一块是任务原文、PO 一单一块。
    """
    from llm.llm_purchase_draft import build_purchase_prompt
    from llm.llm_receipt_draft import build_receipt_prompt

    materials, warehouses, orders = master_data
    utterance = "给 WH01 补 3000kg 冷轧钢板 1.2mm"

    purchase_view = PromptView(build_purchase_prompt(utterance, materials, warehouses))
    assert purchase_view.find("materials"), "候选物料块要以 {'materials': [...]} 顶层嵌入"
    assert purchase_view.find("warehouses"), "候选仓库块要以 {'warehouses': [...]} 顶层嵌入"
    assert purchase_view.last_block() == utterance, (
        "任务原文必须是最后一个数据块（模板末尾那道围栏不是装饰）"
    )

    # 收货：每个 PO 自己是一个顶层块，po_no 在顶层 —— 合成器按它反查物料与到货仓。
    # 包成 {"purchase_orders": [...]} 的话反查会全部落空，且不报任何错。
    po = orders[0]
    receipt_view = PromptView(
        build_receipt_prompt(f"{po.po_no} 到货了", materials, warehouses, orders)
    )
    top_level_hits = [
        b for b in receipt_view.non_schema_dicts() if b.get("po_no") == po.po_no
    ]
    assert top_level_hits, (
        f"{po.po_no} 的 PO 块不在顶层：被包进了嵌套结构，物料编码会悄悄变空"
    )


# ==================== 收货登记要素提取 ====================

def test_receipt_draft_extracts_elements_from_the_real_prompt(master_data):
    """到货登记走真实提示词：数量/不良类型从原文里来，物料与仓库由 PO 块反查。"""
    from llm.llm_receipt_draft import llm_receipt_draft

    materials, warehouses, orders = master_data
    po = next(o for o in orders if o.po_no == "PO20260928002")

    draft = llm_receipt_draft(
        "PO20260928002 到货 920 支，开箱抽检发现 30 支磕碰变形",
        materials, warehouses, orders,
    )

    assert draft.po_no == "PO20260928002"
    assert (draft.qty_received, draft.qty_defect) == (920, 30)
    assert draft.defect_type == "磕碰变形"
    # 登记话里没写物料编码和仓库 —— 从命中的那张 PO 上取，而不是凭常识猜
    assert draft.material_code == po.material_code
    assert draft.warehouse_code == po.target_warehouse


def test_receipt_draft_keeps_zero_defect_as_zero_not_empty(master_data):
    """「没发现问题」是明确结论，不是缺数据：不良数记 0，不能让上层追问。"""
    from llm.llm_receipt_draft import llm_receipt_draft

    materials, warehouses, orders = master_data
    draft = llm_receipt_draft(
        "PO20260928002 到货了，实收 1000 支，没有发现问题",
        materials, warehouses, orders,
    )

    assert draft.qty_received == 1000
    assert draft.qty_defect == 0
    assert draft.defect_type == ""


# ==================== 降级契约 ====================

def test_unrecognized_prompt_degrades_to_empty_object_instead_of_crashing():
    """认不出目标结构的提示词：返回空对象，而不是抛异常把图打崩。

    `{}` 对调用方是「没合成出来」，随后由各自的默认值兜住；抛异常则会中断
    整条链路 —— 降级顺序是：合成结果 -> 示例实例 -> 空对象。
    """
    assert json.loads(build_payload("这是一段没有任何 schema 定义的普通文本。")) == {}
