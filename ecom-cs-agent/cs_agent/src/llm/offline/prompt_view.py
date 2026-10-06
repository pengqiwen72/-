"""
PromptView —— 把现有提示词「反向解析」成结构化视图。

为什么需要它
------------
项目里所有 `llm_*.py` 都遵循同一套约定：把目标 Pydantic 模型的 `{schema_json}` /
`{example_json}` 内嵌进提示词，再把业务数据块 `{chat_history}` / `{orders}` / ... 格式化进去。

可这带来一个机会：提示词本身就是**自描述的**。离线合成器只要能解析出
「这次要产出哪个模型」+「输入数据是什么」，就能合成出合法的结构化输出，
而不必让 7 个调用方各写一套 mock —— 调用方零改动。

解析要点
--------
不能靠正则匹配 `"title"`：pydantic 的 `model_json_schema()` 会为**每个字段**渲染 title，
且 `$defs` 排在最前面，第一个 `"title"` 往往是字段名（如 "Tag Id"）而非类名。
正确做法是用 `json.JSONDecoder.raw_decode` 提取**顶层** JSON 值——`$defs` 是嵌套的，
不会被单独提取出来，因此顶层 schema 的 `title` 就是类名。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# 现有提示词统一用 "-------------------" 作为数据块分隔线
FENCE = re.compile(r"^-{3,}\s*$", re.MULTILINE)


class PromptView:
    """提示词的结构化视图（惰性解析，同一个 prompt 只解析一次）。"""

    def __init__(self, raw: str):
        self.raw = raw or ""
        self._blobs: Optional[list[Any]] = None

    # ---------- 基础解析 ----------

    def blobs(self) -> list[Any]:
        """提取提示词中所有**顶层** JSON 值（对象/数组）。

        用 raw_decode 顺序扫描，避免手写括号配平（字符串里的括号、转义都会坑）。
        """
        if self._blobs is not None:
            return self._blobs

        out: list[Any] = []
        decoder = json.JSONDecoder()
        i = 0
        n = len(self.raw)

        while i < n:
            # 找下一个可能的 JSON 起点
            brace = self.raw.find("{", i)
            bracket = self.raw.find("[", i)
            candidates = [p for p in (brace, bracket) if p != -1]
            if not candidates:
                break
            start = min(candidates)

            try:
                obj, end = decoder.raw_decode(self.raw, start)
            except ValueError:
                # 这个位置不是合法 JSON 起点，跳过继续找
                i = start + 1
                continue

            out.append(obj)
            i = end

        self._blobs = out
        return out

    def schemas(self) -> list[dict]:
        """看起来像「根 JSON Schema」的块：有 properties + 字符串 title。

        顶层提取保证了这里的 title 是类名，而 $defs 里的嵌套定义不会出现在这一层。
        """
        return [
            b
            for b in self.blobs()
            if isinstance(b, dict)
            and "properties" in b
            and isinstance(b.get("title"), str)
        ]

    def non_schema_dicts(self) -> list[dict]:
        """排除 schema 之后的普通字典块——业务输入数据都在这里。"""
        return [
            b
            for b in self.blobs()
            if isinstance(b, dict) and not ("properties" in b and "title" in b)
        ]

    # ---------- 语义解析 ----------

    def detect_target_model(self, key_signatures: dict[str, set[str]]) -> Optional[str]:
        """判断本次要产出哪个模型。

        1. 优先用 schema 根 title（就是类名）精确匹配
        2. 退而求其次：用示例 JSON 的字段签名匹配
        """
        for schema in self.schemas():
            title = schema.get("title")
            if isinstance(title, str) and title in key_signatures:
                return title

        # 兜底：按示例对象的字段集合识别
        for block in self.blobs():
            if not isinstance(block, dict):
                continue
            keys = set(block)
            for name, signature in key_signatures.items():
                if signature and signature <= keys:
                    return name
        return None

    def find(self, *keys: str) -> Optional[dict]:
        """找出第一个包含全部指定键的**非 schema** 字典块。

        各业务模型的顶层键在提示词里是唯一的（chat_msgs / orders / tag_setting ...），
        所以可以据此反查输入数据，无需给每个合成器定制解析逻辑。

        ⚠️ 「唯一」有一个例外：输入数据的顶层键与目标模型的 `{example_json}` 撞名时
        （画像：示例实例和旧画像的顶层键都是 `profile_items`），这个函数会先撞上
        示例实例。要取真实数据请用 `find_last`。
        """
        for block in self.non_schema_dicts():
            if set(keys) <= set(block):
                return block
        return None

    def find_last(self, *keys: str) -> Optional[dict]:
        """找出**最后一个**包含全部指定键的非 schema 字典块。

        专治「输入数据块与 `{example_json}` 顶层键撞名」：提示词模板里示例排在业务
        数据块之前（`{example_json}` → chat_history → ... → `{customer_profile}`），
        所以撞名时真实数据永远是最后一块。画像合成器踩过这个坑 —— 用 `find` 会把
        示例实例当成旧画像，存量项被示例项顶掉、示例文案混进结果。
        """
        hit: Optional[dict] = None
        for block in self.non_schema_dicts():
            if set(keys) <= set(block):
                hit = block
        return hit

    def find_list(self, *keys: str) -> Optional[list]:
        """找出第一个「元素为字典且包含指定键」的列表块。"""
        for block in self.blobs():
            if isinstance(block, list) and block and isinstance(block[0], dict):
                if set(keys) <= set(block[0]):
                    return block
        return None

    # ---------- 文本块 ----------

    def fenced(self) -> list[str]:
        """按分隔线切开，得到各个数据块文本。"""
        return FENCE.split(self.raw)

    def last_block(self) -> str:
        """最后一个数据块——按现有提示词约定，这里是本次的任务输入。"""
        parts = self.fenced()
        if len(parts) > 1:
            return parts[-2].strip()
        return ""

    def first_block(self) -> str:
        parts = self.fenced()
        if len(parts) > 1:
            return parts[1].strip()
        return ""

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<PromptView schemas={[s.get('title') for s in self.schemas()]}>"
