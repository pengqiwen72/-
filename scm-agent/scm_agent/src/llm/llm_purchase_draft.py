"""
采购申请要素提取（把自然语言申请抽成 PurchaseDraft）。

模型在这里只做一件事：**抽取**。「要不要审批、走哪条策略」是确定性规则引擎的
事（见 `integrations/policy.py` 的口径）——抽取错一个字段还能澄清，判定错一次
就是真金白银，两类错误不能共用一个失效模式。

与离线合成器的契约（改提示词时三条都不能破）
--------------------------------------------
`llm/offline/synth_scm.py` 按**同一份提示词**反向解析出输入数据，所以：

  1. 候选物料/仓库要以 `{"materials": [...]}` / `{"warehouses": [...]}` 的
     顶层 JSON 块嵌入 —— 合成器用 `PromptView.find("materials")` 取；
  2. 提示词以 `-------------------` 结尾，任务原文是**最后一个**数据块 ——
     `PromptView.last_block()` 取的是倒数第二段（末尾那道围栏不是装饰）；
  3. `{schema_json}` / `{example_json}` 必须保留 —— 目标模型的识别靠 schema
     根 title（类名），这也是合成器分派的唯一依据。
"""

from __future__ import annotations

import json
from typing import List

from langchain_core.messages import AIMessage

from llm.llm_setting import chat_model as llm
from models.scm_models import MaterialInfo, PurchaseDraft, WarehouseInfo
from utils.agent_logger import get_logger

logger = get_logger("llm_purchase_draft")


def build_purchase_prompt(
    task_input: str,
    materials: List[MaterialInfo],
    warehouses: List[WarehouseInfo],
) -> str:
    """拼提示词。

    单独抽成函数是为了离线模式能用**同一份提示词**喂合成器 ——
    线上离线各拼一份的话，两边会悄悄分叉，离线结果也就没有参考价值了。
    """
    return _purchase_draft_instructions.format(
        materials=json.dumps(
            {"materials": [m.model_dump() for m in materials]},
            ensure_ascii=False, indent=4,
        ),
        warehouses=json.dumps(
            {"warehouses": [w.model_dump() for w in warehouses]},
            ensure_ascii=False, indent=4,
        ),
        schema_json=PurchaseDraft.get_schema_json(),
        example_json=PurchaseDraft.get_example_json(),
        task_input=task_input,
    )


def llm_purchase_draft(
    task_input: str,
    materials: List[MaterialInfo],
    warehouses: List[WarehouseInfo],
) -> PurchaseDraft:
    """抽取采购申请要素；抽不到的字段留空，由上层澄清（不猜）。"""
    prompt = build_purchase_prompt(task_input, materials, warehouses)

    logger.debug(f"prompt: {prompt}")

    generated_result: AIMessage = llm.invoke(prompt)

    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        draft_json = generated_result.content
    else:
        draft_json = "{}"

    try:
        draft = PurchaseDraft.model_validate_json(draft_json)
        logger.info(f"purchase_draft: {draft.model_dump_json()}")
        return draft
    except Exception as e:
        logger.error(f"解析采购申请要素失败: {e}")
        return PurchaseDraft()


_purchase_draft_instructions = """
你的任务是把用户的采购申请口语，抽取成结构化要素：PurchaseDraft。

这里是 PurchaseDraft 的数据结构定义：
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
4、【重要】material_code 必须从下面的候选物料里选，选不到就留空 —— 不要编造编码。
5、target_warehouse 必须是候选仓库里的编码；用户说的是仓库别称（如「一号仓」）时，
   能对上哪个就填哪个，对不上就留空，由上层追问。
6、数量里的「吨」换算成 kg 后再填（1 吨 = 1000 kg），unit 填 kg。
7、reason 原样保留用户的说明，不要改写、不要补充用户没说过的理由。

下面是候选数据：

1、候选物料（material_code / material_name / spec / unit 等）：
-------------------
{materials}
-------------------

2、候选仓库：
-------------------
{warehouses}
-------------------

用户这次的采购申请原文：
-------------------
{task_input}
-------------------

"""
