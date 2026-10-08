"""
收货登记要素提取（把自然语言到货登记抽成 ReceiptDraft）。

与采购申请同一条边界：模型只**抽取**（单号、实收、不良、不良类型），
不良率算不算超标、要不要人工复核，全部交给确定性规则（策略引擎 + 阈值）。

与离线合成器的契约
------------------
除 `llm_purchase_draft.py` 里那三条通用约定外，这里多一条**关键**约定：

  候选采购订单要**一单一块**地嵌入（每个 PO 自己是一个顶层 JSON 对象，
  `po_no` 在顶层）。合成器按 `block.get("po_no") == 本次单号` 反查物料与
  到货仓 —— 如果把 PO 包成 `{"purchase_orders": [...]}` 一个大块，
  `po_no` 就沉到嵌套层里，反查会全部落空，物料编码悄悄变成空。
"""

from __future__ import annotations

import json
from typing import List

from langchain_core.messages import AIMessage

from llm.llm_setting import chat_model as llm
from models.scm_models import MaterialInfo, PurchaseOrderInfo, ReceiptDraft, WarehouseInfo
from utils.agent_logger import get_logger

logger = get_logger("llm_receipt_draft")


def build_receipt_prompt(
    task_input: str,
    materials: List[MaterialInfo],
    warehouses: List[WarehouseInfo],
    purchase_orders: List[PurchaseOrderInfo],
) -> str:
    """拼提示词（线上离线共用这一份）。"""
    po_blocks = "\n-------------------\n".join(
        json.dumps(po.model_dump(), ensure_ascii=False, indent=4)
        for po in purchase_orders
    ) or "{}"

    return _receipt_draft_instructions.format(
        materials=json.dumps(
            {"materials": [m.model_dump() for m in materials]},
            ensure_ascii=False, indent=4,
        ),
        warehouses=json.dumps(
            {"warehouses": [w.model_dump() for w in warehouses]},
            ensure_ascii=False, indent=4,
        ),
        purchase_orders=po_blocks,
        schema_json=ReceiptDraft.get_schema_json(),
        example_json=ReceiptDraft.get_example_json(),
        task_input=task_input,
    )


def llm_receipt_draft(
    task_input: str,
    materials: List[MaterialInfo],
    warehouses: List[WarehouseInfo],
    purchase_orders: List[PurchaseOrderInfo],
) -> ReceiptDraft:
    """抽取收货登记要素；抽不到的字段留空，由上层澄清（不猜）。"""
    prompt = build_receipt_prompt(task_input, materials, warehouses, purchase_orders)

    logger.debug(f"prompt: {prompt}")

    generated_result: AIMessage = llm.invoke(prompt)

    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        draft_json = generated_result.content
    else:
        draft_json = "{}"

    try:
        draft = ReceiptDraft.model_validate_json(draft_json)
        logger.info(f"receipt_draft: {draft.model_dump_json()}")
        return draft
    except Exception as e:
        logger.error(f"解析收货登记要素失败: {e}")
        return ReceiptDraft()


_receipt_draft_instructions = """
你的任务是把用户的收货登记口语，抽取成结构化要素：ReceiptDraft。

这里是 ReceiptDraft 的数据结构定义：
-------------------
{schema_json}
-------------------

json 对象示例：
-------------------
{example_json}
-------------------

抽取要求：

1、【重要】您必须回复一个有效 JSON 对象
2、请不要在 JSON 对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
3、请不要在 json 对象中包含任何未定义的字段。
4、【重要】po_no 必须是下面候选采购订单里的单号，对不上就留空 —— 不要编造单号。
5、material_code / warehouse_code 用户没写时，从命中的那张采购订单里取（订单上写着
   这笔货的物料和到货仓库），不要凭常识猜。
6、qty_received 是**实收**数量，qty_defect 是其中**不良**的数量；两者是包含关系，
   不良数不可能大于实收数。
7、defect_type 用「磕碰变形 / 尺寸超差 / 包装破损 / 表面划伤 / 氧化变色 / 受潮生锈」
   这类标准说法；用户没提不良时留空。
8、description 原样保留用户的说明，不要改写。

下面是候选数据：

1、候选物料：
-------------------
{materials}
-------------------

2、候选仓库：
-------------------
{warehouses}
-------------------

3、候选采购订单（每笔一块，按 po_no 认单）：
-------------------
{purchase_orders}
-------------------

用户这次的收货登记原文：
-------------------
{task_input}
-------------------

"""
