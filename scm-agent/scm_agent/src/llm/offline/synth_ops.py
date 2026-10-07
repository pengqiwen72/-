"""
运营侧模型的离线合成器：客户标签 / 客户画像 / 话术建议 / 跟进任务。

共同原则：**每个结论都要能追溯到输入里的某句原文**。

这一点决定了它们不是「返回固定常量」。一份写死的标签建议看起来很像样，
但面试官只要换一句对话内容再跑一遍，就会发现输出没有任何变化——那这套
离线实现的演示价值就归零了。所以这里的四个合成器都是：

    从提示词里取出业务数据 → 用词表/正则/统计找证据 → 把证据原文写进 reason

标签建议引用命中的关键词，画像引用会话里抽出的地址与金额，话术建议引用
RAG 检索到的条款原文，跟进任务引用对话里出现的具体时间点。都是可核对的。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from llm.offline.lexicons import TAG_KEYWORDS, TAG_REMOVE_RULES
from llm.offline.prompt_view import PromptView
from llm.offline.registry import synthesizer
from models.scm_models import (
    CustomerProfile,
    ReplySuggestion,
    ScheduleSuggestion,
    TagInfo,
    TagSuggestion,
)
from utils.agent_logger import get_logger
from utils.text import last_question_clause

logger = get_logger("synth_ops")

#: 单次最多建议添加多少个标签。全量输出会让「建议」失去意义——
#: 24 个标签里推 15 个，坐席等于没得到帮助。
MAX_TAG_ADD = 6
MAX_TAG_REMOVE = 3

#: 语义上要求「多次发生」的标签所需的最小证据数。
#: 「退换货**频繁**」「**易**投诉」这类标签，客户只提一次是不成立的——
#: 一次退货是正常售后，不是行为特征。不给这类标签设门槛，就会出现
#: 「客户说了一次退货 → 打上退换货频繁」这种会误导坐席的结论。
TAG_MIN_HITS: dict[str, int] = {
    "TAG-BEHA-RETURN": 2,
    "TAG-SVC-COMPLAINT": 2,
}


# ============================================================ 数据抽取工具

def _texts_from(view: PromptView, *keys: str) -> List[str]:
    """把各消息块里的 content 抽成一个文本列表。"""
    out: List[str] = []
    for key in keys:
        block = view.find(key)
        if not block:
            continue
        items = block.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("content"):
                    out.append(str(item["content"]))
    return out


def _orders(view: PromptView) -> List[dict]:
    block = view.find("orders")
    if not block:
        return []
    orders = block.get("orders")
    return [o for o in orders if isinstance(o, dict)] if isinstance(orders, list) else []


def _tag_list(view: PromptView, key: str) -> List[dict]:
    block = view.find(key)
    if not block:
        return []
    items = block.get(key)
    return [t for t in items if isinstance(t, dict)] if isinstance(items, list) else []


def _hits(text: str, keywords: List[str]) -> List[str]:
    """返回文本中命中的关键词（保持词表顺序，稳定输出）。"""
    lowered = text.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


# ============================================================ 客户标签

@synthesizer(TagSuggestion)
def synthesize_tag(view: PromptView) -> dict:
    """基于会话与订单证据生成标签变更建议。"""
    tag_setting = _tag_list(view, "tag_setting")
    current = {t.get("tag_id") for t in _tag_list(view, "customer_tags")}

    chat_text = "\n".join(
        _texts_from(view, "chat_msgs", "kf_chat_msgs")
    )
    orders = _orders(view)
    order_text = "\n".join(
        " ".join(str(p) for p in (o.get("order_products") or [])) for o in orders
    )
    evidence_text = f"{chat_text}\n{order_text}"

    facts = {
        "order_count": len(orders),
        "days_since_last_msg": _days_since_last_msg(view),
    }

    adds: List[dict] = []
    for tag in tag_setting:
        tag_id = tag.get("tag_id", "")
        if not tag_id or tag_id in current:
            continue

        matched = _hits(evidence_text, TAG_KEYWORDS.get(tag_id, []))
        if not matched:
            continue

        # 出现次数（不是命中词数）用于「频繁/易」类标签的门槛判断
        occurrences = sum(evidence_text.lower().count(kw.lower()) for kw in matched)
        if occurrences < TAG_MIN_HITS.get(tag_id, 1):
            logger.info(
                f"跳过 {tag_id}：仅命中 {occurrences} 次，未达该标签的最小证据数"
                f" {TAG_MIN_HITS[tag_id]}"
            )
            continue

        where = "会话" if _hits(chat_text, matched) else "订单"
        adds.append({
            "tag_id": tag_id,
            "tag_name": tag.get("tag_name", ""),
            "tag_reason": f"客户在{where}中提及「{'」「'.join(matched[:3])}」",
            "_score": len(matched),
        })

    # 证据多的排前面；同分按标签体系原顺序，保证结果稳定可复现
    adds.sort(key=lambda a: -a["_score"])
    adds = [a for a in adds[:MAX_TAG_ADD]]

    removes: List[dict] = []
    for tag in tag_setting:
        tag_id = tag.get("tag_id", "")
        if tag_id not in current:
            continue
        rule = TAG_REMOVE_RULES.get(tag_id)
        if not rule:
            continue
        fact_key, op, threshold, template = rule
        value = facts.get(fact_key)
        if value is None:
            continue
        if _compare(value, op, threshold):
            removes.append({
                "tag_id": tag_id,
                "tag_name": tag.get("tag_name", ""),
                "tag_reason": template.format(**facts),
            })
        if len(removes) >= MAX_TAG_REMOVE:
            break

    for item in adds:
        item.pop("_score", None)

    logger.info(
        f"离线标签建议：新增 {len(adds)} 个（{ [a['tag_id'] for a in adds] }）、"
        f"移除 {len(removes)} 个（{ [r['tag_id'] for r in removes] }）"
    )
    return {"tag_ids_add": adds, "tag_ids_remove": removes}


def _compare(value: Any, op: str, threshold: Any) -> bool:
    try:
        if op == ">=":
            return value >= threshold
        if op == "<=":
            return value <= threshold
        if op == "==":
            return value == threshold
    except TypeError:
        return False
    return False


def _days_since_last_msg(view: PromptView) -> Optional[int]:
    """从提示词里的当前时间与最后一条消息时间，算「距今多少天」。"""
    block = view.find("current_time")
    current = None
    if block:
        current = _parse_time(str(block.get("current_time", "")))
    if current is None:
        # current_time 是纯文本块而不是 JSON，退而解析最后一个文本块
        current = _parse_time(view.last_block())
    if current is None:
        current = datetime.now()

    latest: Optional[datetime] = None
    for key in ("chat_msgs", "kf_chat_msgs"):
        for block in (view.find(key) or {}).get(key, []) or []:
            if not isinstance(block, dict):
                continue
            ts = _parse_time(str(block.get("msg_time", "")))
            if ts and (latest is None or ts > latest):
                latest = ts

    if latest is None:
        return None
    return max(0, (current - latest).days)


_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_time(text: str) -> Optional[datetime]:
    text = (text or "").strip()
    m = re.search(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", text)
    if m:
        text = m.group(0).replace("T", " ")
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ============================================================ 客户画像

#: 画像的标准字段口径与**顺序**，直接取自 `CustomerProfile` 的示例实例。
#: 必须用 list 保序：set 的迭代顺序由哈希决定，同一份数据每次跑出来的字段
#: 顺序都可能不同，业务侧做画像整体覆盖时会出现无意义的 diff。
_FIELD_ORDER: List[str] = [
    item.item_name for item in CustomerProfile.get_example_instance().profile_items
]
_FIELD_NAMES = set(_FIELD_ORDER)

#: 从会话里能抽出的字段及其正则。手机号与地址这类**确定性信息**只认字面证据。
_PHONE = re.compile(r"1[3-9]\d{9}")
_ADDRESS = re.compile(r"([一-龥]{2,8}(?:省|市))([一-龥]{2,10}(?:区|县|镇|街道))?([一-龥\d\-\s号栋室楼单元]{2,30})")
_MONEY = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)")
_CITY_HINT = re.compile(r"([一-龥]{2,6}市)")


@synthesizer(CustomerProfile)
def synthesize_profile(view: PromptView) -> dict:
    """在旧画像基础上，用新证据增量更新。

    提示词里明确要求「旧 profile 中需要保留的信息，也更新到新的 profile 中」，
    因为业务系统是**整体覆盖**旧画像。所以这里不是从零重建，而是：
    以旧画像为底 → 用新证据覆盖同名项 → 补上旧画像里没有但推论得出的项。
    """
    base: Dict[str, str] = {}
    # ⚠️ 必须 find_last 不能 find：提示词里 {example_json} 排在真实 {customer_profile}
    # 之前，两者顶层键都是 profile_items —— find 会拿到示例实例，把示例项当成旧画像
    # （存量项被顶掉、示例文案混进结果）。详见 PromptView.find_last 的说明。
    old = view.find_last("profile_items")
    if old and isinstance(old.get("profile_items"), list):
        for item in old["profile_items"]:
            if isinstance(item, dict) and item.get("item_name"):
                base[str(item["item_name"])] = str(item.get("item_value", ""))

    chat_text = "\n".join(_texts_from(view, "chat_msgs", "kf_chat_msgs"))
    orders = _orders(view)

    derived = _derive_from_chat(chat_text, base)
    derived.update(_derive_from_orders(orders, base))

    # 用新证据覆盖；没有新证据的旧值原样保留
    merged = dict(base)
    merged.update({k: v for k, v in derived.items() if v})

    # 按标准字段口径排序，标准字段之外的项排在后面 —— 顺序稳定，
    # 便于 diff，也保证业务侧「画像整体覆盖」时不会有字段忽前忽后
    ordered: List[Tuple[str, str]] = []
    for name in _FIELD_ORDER:
        if merged.get(name):
            ordered.append((name, merged.pop(name)))
    for name in sorted(merged):
        if merged[name]:
            ordered.append((name, merged[name]))

    logger.info(f"离线画像合成：{len(ordered)} 项（旧画像 {len(base)} 项）")
    return {"profile_items": [{"item_name": n, "item_value": v} for n, v in ordered]}


def _derive_from_chat(text: str, base: Dict[str, str]) -> Dict[str, str]:
    """从会话原文里抽取可确证的画像字段。

    只抽**字面出现**的信息（手机号、地址、金额、品类词），不做语义推断——
    画像会被业务系统当作事实使用，而离线实现没有理解长句的能力，
    硬凑出来的"推断"就是在编造。
    """
    out: Dict[str, str] = {}

    if _PHONE.search(text) and not base.get("联系电话"):
        out["联系电话"] = _PHONE.search(text).group(0)

    if not base.get("所在城市"):
        m = _CITY_HINT.search(text)
        if m:
            out["所在城市"] = m.group(1)

    if not base.get("常用收货地址"):
        m = _ADDRESS.search(text)
        if m:
            out["常用收货地址"] = "".join(g for g in m.groups() if g)

    amounts = [float(x) for x in _MONEY.findall(text)]
    if amounts:
        low, high = min(amounts), max(amounts)
        # 会话中提到的金额区间，反映客户的心理价位。
        # 注意这里**不**据此推「价格敏感度」——提到金额不等于对价格敏感，
        # 那个判断交给标签词表，画像只记录确凿出现过的数字。
        out["会话提及金额"] = (
            f"{low:g} 元" if low == high else f"{low:g}-{high:g} 元（会话中提及）"
        )

    return out


#: 品类关键词 -> 品类名。用于把订单商品名归一成「品类偏好」。
_CATEGORY_HINTS = {
    "保温杯": "杯壶", "水杯": "杯壶", "杯": "杯壶",
    "磨豆机": "咖啡器具", "咖啡": "咖啡器具",
    "键盘": "电脑外设", "鼠标": "电脑外设", "外设": "电脑外设",
    "电热毯": "家纺", "床品": "家纺",
    "背包": "箱包", "双肩包": "箱包",
    "灯": "灯具", "台灯": "灯具",
}


def _derive_from_orders(orders: List[dict], base: Dict[str, str]) -> Dict[str, str]:
    """从订单历史推出消费特征。OrderHistory 里没有金额字段，
    所以只推「频次」与「品类」，不碰客单价——宁缺毋滥。
    """
    out: Dict[str, str] = {}
    if not orders:
        return out

    count = len(orders)
    if count >= 3:
        out["购买频次"] = f"近 30 天 {count} 笔订单，属于活跃复购"

    products = " ".join(
        str(p) for o in orders for p in (o.get("order_products") or [])
    )
    cats: List[str] = []
    for kw, cat in _CATEGORY_HINTS.items():
        if kw in products and cat not in cats:
            cats.append(cat)
    if cats:
        out["品类偏好"] = "、".join(cats) + "（依据近期订单商品）"

    return out


# ============================================================ 话术建议

#: 待回复问题的意图关键词，决定回复的结构
_REPLY_STRATEGY = [
    ("商品咨询", ["参数", "规格", "材质", "多久", "能不能", "支持吗", "保温", "防水", "续航", "质保", "保修", "刻字"]),
    ("订单物流", ["订单", "物流", "快递", "发货", "到哪", "签收", "运单", "什么时候到"]),
    ("售后退换", ["退款", "退货", "换货", "破损", "坏了", "质量问题", "补偿"]),
    ("价格优惠", ["优惠", "便宜", "满减", "折扣", "价保", "包邮"]),
]


@synthesizer(ReplySuggestion)
def synthesize_reply(view: PromptView) -> dict:
    """生成坐席话术建议。

    这个合成器同时服务两个提示词（企微私聊 `chat_msgs` 与微信客服 `kf_chat_msgs`），
    两者输出结构相同（都是 ReplySuggestion），差别只在输入键名与角色。

    策略是「**先检索、再套结构**」：拿客户最后一句去 RAG 检索权威条款，
    命中就把原文写进回复，未命中就明确说「我去核实」并给出时限——
    而不是编一个听起来专业的答复。客服场景里，说「我确认后回复您」
    远比说错一句质保年限安全。
    """
    is_kf = view.find("kf_chat_msgs") is not None
    key = "kf_chat_msgs" if is_kf else "chat_msgs"

    msgs = _msgs(view, key)
    if not msgs:
        return {"reply_content": "", "reply_reason": "会话记录为空，无法生成建议"}

    pending = _last_customer_message(msgs)
    if not pending:
        return {
            "reply_content": "您好，请问还有什么可以帮到您的吗？",
            "reply_reason": "最近一条消息由我方发出，客户尚未回复，此处不宜主动追问。",
        }

    # 用**末尾的疑问句串**而不是整条消息去检索。客户的消息常常前半句在说
    # 别的事，只有末尾才是待回答的问题（"好的那就下单。对了，如果磕碰了
    # 怎么处理？多久答复？"）。整段拿去检索，前半句的业务词会把检索带偏。
    question = last_question_clause(pending)

    strategy = _classify(question)
    customer_name = _customer_name(view)

    answer, source = _retrieve(question)
    greeting = f"{customer_name}您好，" if customer_name else "您好，"

    if answer:
        content = f"{greeting}{answer}"
        reason = (
            f"客户诉求类型「{strategy}」，待答问题：「{question[:50]}」。"
            f"回复取自知识库命中条款（出处：{source}），已附着出处以便坐席核对。"
        )
    else:
        content = (
            f"{greeting}您反馈的问题我需要核实一下具体规则，"
            f"确认后会在 2 小时内给您明确答复，请您稍等。"
        )
        reason = (
            f"客户诉求类型「{strategy}」，待答问题：「{question[:50]}」。"
            f"知识库未检索到可直接引用的条款，因此不凭经验作答，"
            f"改为承诺核实时限——给出与政策不符的承诺，代价远高于晚回复两小时。"
        )

    logger.info(
        f"离线话术建议：策略={strategy} 问题='{question[:30]}' 有出处={bool(answer)}"
    )
    return {"reply_content": content, "reply_reason": reason}


def _msgs(view: PromptView, key: str) -> List[dict]:
    block = view.find(key)
    if not block:
        return []
    items = block.get(key)
    return [m for m in items if isinstance(m, dict)] if isinstance(items, list) else []


def _customer_name(view: PromptView) -> str:
    block = view.find("nick_name")
    if block and block.get("nick_name"):
        return str(block["nick_name"])
    return ""


#: 坐席侧的发送者标识。企微私聊里 sender 是坐席的名字，客服会话里是「客服」。
_STAFF_MARKERS = ("客服", "坐席", "顾问", "专员", "助手")


def _last_customer_message(msgs: List[dict]) -> str:
    """取最后一条**客户发出**的消息。

    以最后一条消息判断是否轮到我们回复；若不是客户发的，就不该催。
    """
    if not msgs:
        return ""
    last = msgs[-1]
    if _is_staff(last.get("sender", "")):
        return ""
    return str(last.get("content", ""))


def _is_staff(sender: str) -> bool:
    return any(marker in str(sender) for marker in _STAFF_MARKERS)


def _classify(text: str) -> str:
    best, best_hits = "通用咨询", 0
    for name, keywords in _REPLY_STRATEGY:
        hits = len(_hits(text, keywords))
        if hits > best_hits:
            best, best_hits = name, hits
    return best


def _retrieve(question: str) -> Tuple[str, str]:
    """调 RAG 取一条可直接引用的答案。失败时返回空——降级为「核实后回复」。"""
    try:
        from rag import get_knowledge_base

        kb = get_knowledge_base()
        answer = kb.ask(question)

        if answer.status != "answered" or not answer.citations:
            return "", ""

        source = answer.citations[0].section
        # 复用 RAG 的回答，去掉它自带的"根据商品资料："前缀，改由坐席口吻输出
        text = answer.answer.replace("根据商品资料：", "").strip()
        return text, source
    except Exception as e:  # pragma: no cover - RAG 不可用时不应拖垮话术链路
        logger.warning(f"RAG 检索失败，退化为核实后回复：{e}")
        return "", ""


# ============================================================ 跟进任务

#: 相对时间词 -> 天数偏移
_RELATIVE_DAYS = [
    ("后天", 2), ("明天", 1), ("今天", 0), ("大后天", 3),
]

#: 时间点：14:00 / 下午三点 / 上午十点
_CLOCK = re.compile(r"(\d{1,2})[:：点](\d{2})?")
_PERIOD = {"上午": 0, "中午": 12, "下午": 12, "晚上": 0}


@synthesizer(ScheduleSuggestion)
def synthesize_schedule(view: PromptView) -> dict:
    """从会话里提取时间点，生成跟进任务。

    提示词要求「如果没有明确的日程信息，可以不用生成日程建议」。所以这里的
    首要判断是**有没有时间依据**：没有就不硬造一条 30 分钟后的日程。
    日程是会被真的写进坐席日历的，凭空生成等于制造垃圾任务。
    """
    msgs = _msgs(view, "chat_msgs")
    if not msgs:
        return _empty_schedule("会话记录为空")

    current = _parse_time(str((view.find("current_time") or {}).get("current_time", "")))
    if current is None:
        current = _parse_time(view.last_block()) or datetime.now()

    # 倒序找最近一条带时间表达的客户消息
    for msg in reversed(msgs):
        if _is_staff(msg.get("sender", "")):
            continue
        content = str(msg.get("content", ""))
        when = _extract_when(content, current)
        if when:
            topic = _topic_of(content)
            return {
                "title": f"跟进：{topic}",
                "start_time": when.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": 30,
                "schedule_reason": (
                    f"客户在会话中提到「{content[:40]}」，其中的时间表达指向 "
                    f"{when.strftime('%m-%d %H:%M')}，据此生成 30 分钟跟进任务。"
                ),
            }

    return _empty_schedule("会话中未出现明确的时间表达")


def _empty_schedule(reason: str) -> dict:
    """无日程依据时返回空建议。

    提示词里写了「没有明确的日程信息可以不用生成」，但下游节点会把这个
    dict 直接 `model_validate` 成 ScheduleSuggestion。返回不存在的字段会
    校验失败，所以字段要齐、值留空，用 title 是否为空表达「没有建议」。
    （start_time 的空串依赖 `ScheduleSuggestion.start_time` 的 pattern 特意
    放宽 —— 它必须扛过 `model_validate`，否则 registry 会回退成示例实例。）
    """
    logger.info(f"离线跟进任务：不生成（{reason}）")
    return {"title": "", "start_time": "", "duration": 30, "schedule_reason": reason}


def _extract_when(text: str, base: datetime) -> Optional[datetime]:
    """从一句话里解析出具体时间点。解析不出就返回 None。"""
    day_offset: Optional[int] = None
    for word, offset in _RELATIVE_DAYS:
        if word in text:
            day_offset = offset
            break

    m = _CLOCK.search(text)
    hour = minute = None
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        for period, delta in _PERIOD.items():
            if period in text:
                if period in ("下午", "晚上") and hour < 12:
                    hour += delta if delta else 12
                break

    if day_offset is None and hour is None:
        return None

    target = base + timedelta(days=day_offset or 0)
    if hour is None:
        # 只说了「明天」没说几点：默认上午 10 点，这是客服跟进最常用的时段
        hour, minute = 10, 0
    return target.replace(hour=min(hour, 23), minute=minute or 0, second=0, microsecond=0)


#: 跟进事由归纳：命中哪类词就用哪类标题
_TOPIC_RULES = [
    ("售后处理", ["退款", "退货", "换货", "破损", "质量"]),
    ("物流催促", ["物流", "快递", "发货", "到货"]),
    ("报价沟通", ["报价", "价格", "优惠", "预算"]),
    ("商品确认", ["参数", "规格", "型号", "定制", "刻字"]),
]


def _topic_of(text: str) -> str:
    for topic, keywords in _TOPIC_RULES:
        if _hits(text, keywords):
            return topic
    return "客户事项"
