"""
供应链域的离线合成器：采购申请要素提取（PurchaseDraft）、收货登记要素提取（ReceiptDraft）。

为什么只有这两个模型
--------------------
离线合成器的职责是「替模型产出 schema 合法的结构化对象」。供应链域里需要模型
产出结构化 JSON 的只有两处**要素提取**：把「帮我给一号仓补 8000 公斤冷轧钢板」
抽成 PurchaseDraft，把「PO20260928002 到货 920 支，磕碰变形 30 支」抽成
ReceiptDraft。其余链路要么是确定性装配（库存聚合、比价排名），要么走纯文本
RAG（制度问答），都不该在这里出现。

同样重要的是**它们不做什么**：不判断要不要审批、不算金额、不查库存。抽取错一个
字段，上层可以澄清；策略判错一次就是真金白银。把「抽取」和「判断」放进同一个
组件，等于让两类错误共用同一个失效模式 —— 这是这条链路最重要的一条边界。

与 synth_ops / synth_ecom 一致，这里坚持**数据驱动**：物料编码/名称从提示词
里的候选物料块里来，数字从用户原话里来，不返回固定常量。离线演示的每一个字段
都能在输入里指出来，面试时经得起「这不就是写死的吗」这一问。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from models.scm_models import PurchaseDraft, ReceiptDraft
from llm.offline.prompt_view import PromptView
from llm.offline.registry import synthesizer
from utils.agent_logger import get_logger

logger = get_logger("synth_scm")

# ---------------------------------------------------------------------------
# 通用抽取基元
# ---------------------------------------------------------------------------

#: 编码形态：与种子数据、图谱实体链接共用同一套口径（M\d{6} / S\d{3} / WH\d{2} / PO…）
_RE_MATERIAL_CODE = re.compile(r"\bM\d{6}\b")
_RE_SUPPLIER_CODE = re.compile(r"\bS\d{3}\b")
_RE_WAREHOUSE_CODE = re.compile(r"\bWH\d{2}\b")
_RE_PO_NO = re.compile(r"\bPO\d{8,}\b")

#: 数量 + 单位。单位表刻意不含 mm/m² 这类规格单位 —— 「冷轧钢板 1.2mm」
#: 是规格不是数量，`1.2mm` 绝不能被抽成 qty=1.2。单位只列采购申请里真会用的。
_RE_QTY_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(吨|千克|公斤|kg|KG|Kg|支|个|件|套|卷|米|台|桶)"
)

#: 需要换算成 kg 的单位（模型口径：吨在抽取层就换算掉，下游只见 kg）
_TO_KG = {"吨": 1000}
#: 单位归一化（同义写法收敛到规范单位）
_UNIT_ALIAS = {"千克": "kg", "公斤": "kg", "KG": "kg", "Kg": "kg"}

#: 不良类型词表（长词在前，先匹配到更具体的类型）。
#: 收货登记的 defect_type 直接影响质检口径展示，词表之外的说法留空由上层追问。
_DEFECT_TYPES = (
    "磕碰变形", "尺寸超差", "包装破损", "表面划伤", "氧化变色", "受潮生锈",
    "变形", "锈蚀", "破损", "划伤", "超差", "变色",
)


def _norm_unit(unit: str) -> str:
    return _UNIT_ALIAS.get(unit, unit)


def _like(text: str, *labels: str) -> Optional[float]:
    """在文本里找「标签 + 数字」或「数字 + … + 标签」的数字。

    两种方向都要认：登记习惯既有「实收 920」也有「920 件到货」。
    数字与标签之间最多隔 6 个非数字字符（冒号、量词、「数量」等），
    再远就不再认为两者相关 —— 宁可抽不到让上层追问，不要抽错。
    """
    for label in labels:
        m = re.search(rf"{re.escape(label)}[^\d]{{0,6}}(\d+(?:\.\d+)?)", text)
        if m:
            return float(m.group(1))
        m = re.search(rf"(\d+(?:\.\d+)?)\s*(?:件|个|支|套|台|吨|kg)?[^\d]{{0,6}}{re.escape(label)}", text)
        if m:
            return float(m.group(1))
    return None


def _extract_qty_unit(text: str) -> Tuple[float, str]:
    """抽「数量 + 单位」，吨换算成 kg。取第一个命中（申请原话里数量通常只出现一次）。"""
    m = _RE_QTY_UNIT.search(text)
    if not m:
        return 0.0, ""
    value = float(m.group(1))
    unit = _norm_unit(m.group(2))
    if unit in _TO_KG:
        value *= _TO_KG[unit]
        unit = "kg"
    return value, unit


def _candidates(view: PromptView, key: str, code_field: str, name_field: str) -> List[dict]:
    """提示词里嵌入的候选主数据块（线上提示词与离线共用同一份提示词）。

    没有候选块时返回空 —— 此时只剩编码直命中可用。这是刻意的：合成器不自己
    去读种子数据，否则「提示词里给了什么」和「合成器用了什么」就会悄悄分叉，
    线上离线行为不一致，离线结果再准也没有参考价值。
    """
    block = view.find(key)
    if block and isinstance(block.get(key), list):
        return [x for x in block[key] if isinstance(x, dict)]
    found = view.find_list(code_field, name_field)
    return [x for x in (found or []) if isinstance(x, dict)]


def _match_by_name(candidates: List[dict], text: str,
                   code_field: str, name_field: str) -> Tuple[str, str]:
    """名称匹配：把名称按空白切成段（「冷轧钢板 1.2mm」→ 冷轧钢板 / 1.2mm），
    用户说到任意一段即命中，按命中段总长打分取最优。

    为什么要切段：用户说「冷轧钢板」而主数据全名是「冷轧钢板 1.2mm」，整名包含
    匹配永远命中不了；反过来若用「名称在文本里找前缀」又会对「钢板」这种泛称
    过度命中。切段 + 最长优先是这两个失败模式之间的折中。
    """
    best: Tuple[str, str] = ("", "")
    best_score = 0
    for item in candidates:
        name = str(item.get(name_field) or "")
        if not name:
            continue
        score = sum(len(seg) for seg in name.split() if len(seg) >= 2 and seg in text)
        if score > best_score:
            best_score = score
            best = (str(item.get(code_field) or ""), name)
    return best


def _match_material(view: PromptView, text: str) -> Tuple[str, str]:
    """定物料：编码直命中 > 候选列表里的名称切段匹配。

    返回 (material_code, material_query)。material_query 保留命中的名称原文，
    供上层在澄清话术里回显「你说的是不是这个」。
    """
    m = _RE_MATERIAL_CODE.search(text)
    if m:
        code = m.group(0)
        return code, _name_of(view, code) or code
    return _match_by_name(_candidates(view, "materials", "material_code", "material_name"),
                          text, "material_code", "material_name")


def _match_warehouse(view: PromptView, text: str) -> str:
    """定仓库：编码直命中 > 候选列表里的名称切段匹配（「上海临港仓」这类说法）。"""
    m = _RE_WAREHOUSE_CODE.search(text)
    if m:
        return m.group(0)
    code, _ = _match_by_name(
        _candidates(view, "warehouses", "warehouse_code", "warehouse_name"),
        text, "warehouse_code", "warehouse_name",
    )
    return code


def _name_of(view: PromptView, code: str) -> str:
    for mat in _candidates(view, "materials", "material_code", "material_name"):
        if str(mat.get("material_code") or "") == code:
            return str(mat.get("material_name") or "")
    return ""


def _task_text(view: PromptView) -> str:
    """本次任务输入：提示词最后一个数据块（与 synth_core 的取法一致）。"""
    return view.last_block()


# ---------------------------------------------------------------------------
# 采购申请要素提取
# ---------------------------------------------------------------------------

@synthesizer(PurchaseDraft)
def synthesize_purchase_draft(view: PromptView) -> dict:
    """离线采购要素提取：正则抽编码/数量/仓库/供应商，候选块匹配物料名。

    抽不到的字段留空/留零，**不猜**：上层（sub_purchase_request 的要素校验节点）
    拿到的空字段会走澄清分支 —— 这与线上模型「没听清就问」的行为是同一条路径。
    """
    text = _task_text(view)
    if not text:
        logger.warning("采购提取提示词最后一块是空的，返回空草稿")
        return {}

    material_code, material_query = _match_material(view, text)
    qty, unit = _extract_qty_unit(text)

    sup = _RE_SUPPLIER_CODE.search(text)

    # reason 原样保留用户的提报说明：它是审批人做判断的依据，任何转写都可能丢信息。
    reason = " ".join(text.split())

    return {
        "material_query": material_query,
        "material_code": material_code,
        "qty": qty,
        "unit": unit,
        "target_warehouse": _match_warehouse(view, text),
        "supplier_code": sup.group(0) if sup else "",
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 收货登记要素提取
# ---------------------------------------------------------------------------

def _extract_receipt_counts(text: str) -> Tuple[int, int]:
    """抽实收/不良数量。优先显式标签（实收/到货/收货、不良/缺陷/破损），
    其次「N 件中有 M 件」的并列句式。都抽不到就留 0，让上层追问。"""
    received = _like(text, "实收", "到货", "收货")
    defect = _like(text, "不良", "缺陷", "瑕疵", "发现", "问题")

    if received is None or defect is None:
        # 「920 件中 30 件有问题」/「抽检 920，30 件变形」
        m = re.search(r"(\d+)\s*(?:件|个|支|套|台)?\s*(?:中|其中|里|,|，|、)\s*(?:有)?\s*(\d+)", text)
        if m:
            if received is None:
                received = float(m.group(1))
            if defect is None:
                defect = float(m.group(2))

    if received is None:
        # 最后兜底：「N 支」这种带量词的数量，第一个当真收数
        m = re.search(r"(\d+)\s*(?:件|个|支|套|台)", text)
        received = float(m.group(1)) if m else 0.0

    return int(received or 0), int(defect or 0)


def _extract_defect_type(text: str) -> str:
    for word in _DEFECT_TYPES:
        if word in text:
            return word
    return ""


@synthesizer(ReceiptDraft)
def synthesize_receipt_draft(view: PromptView) -> dict:
    """离线收货要素提取：单号/数量直抽，物料与仓库可从提示词里的 PO 块反查。

    「物料未写明时由 PO 反查」是模型字段自己的口径（见 ReceiptDraft.material_code
    的 description）——线上提示词会把 PO 详情一起给模型，离线同样从这里取。
    """
    text = _task_text(view)
    if not text:
        logger.warning("收货提取提示词最后一块是空的，返回空草稿")
        return {}

    po = _RE_PO_NO.search(text)
    po_no = po.group(0) if po else ""

    # PO 块反查（提示词里以 po_no 为主键的详情块；字段与 purchase_orders.json 同形）。
    # 两个要点：① 必须与本次登记的单号**相等**才采用 —— 示例实例（ReceiptDraft 的
    # {example_json}）同样含 po_no 键，不核对单号就会把示例物料悄悄灌进结果；
    # ② 取最后一个命中 —— 模板里示例排在业务数据之前，真数据永远在后面。
    po_block: Dict[str, Any] = {}
    if po_no:
        for block in reversed(view.non_schema_dicts()):
            if str(block.get("po_no") or "") == po_no:
                po_block = block
                break

    material = _RE_MATERIAL_CODE.search(text)
    material_code = material.group(0) if material else str(po_block.get("material_code") or "")

    warehouse_code = _match_warehouse(view, text) or str(po_block.get("target_warehouse") or "")

    qty_received, qty_defect = _extract_receipt_counts(text)

    return {
        "po_no": po_no,
        "material_code": material_code,
        "qty_received": qty_received,
        "qty_defect": qty_defect,
        "defect_type": _extract_defect_type(text),
        "warehouse_code": warehouse_code,
        "description": " ".join(text.split()),
    }
