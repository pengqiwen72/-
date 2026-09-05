"""
LLM 客户端：OpenAI 兼容 API，含结果解析
"""

import os
import json
import time
from typing import List, Dict, Optional
import requests
from utils import logger, safe_json_parse, extract_json


class LLMClient:
    """通用 LLM 客户端"""

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        self.model = os.getenv("MODEL_NAME", "deepseek-chat")
        self.max_retries = 3

        if not self.api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")

        logger.info("LLM 初始化", {"model": self.model, "url": self.base_url})

    def chat(self, messages: List[Dict], tools: List[Dict] = None) -> str:
        """调用 LLM，返回响应文本"""
        for attempt in range(self.max_retries):
            try:
                return self._call(messages, tools)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error(f"LLM 调用失败: {e}")
                    return f"API 调用失败: {str(e)}"
                wait = 2 ** attempt
                logger.warn(f"重试 {attempt+1}/{self.max_retries}", {"wait": wait})
                time.sleep(wait)
        return "API 调用失败"

    def _call(self, messages: List[Dict], tools: List[Dict] = None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000
        }

        if tools:
            data["tools"] = tools
            data["tool_choice"] = "auto"

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        resp.raise_for_status()

        result = resp.json()
        msg = result["choices"][0]["message"]

        # 工具调用
        if "tool_calls" in msg and msg["tool_calls"]:
            tc = msg["tool_calls"][0]["function"]
            return json.dumps({
                "tool": tc["name"],
                "arguments": safe_json_parse(tc["arguments"])
            })

        return msg["content"]

    def parse_tool_call(self, response: str) -> Optional[Dict]:
        """从响应中提取工具调用"""
        # 直接解析
        data = safe_json_parse(response)
        if data and "tool" in data:
            return data

        # 从文本提取
        data = extract_json(response)
        if data and "tool" in data:
            return data

        # 检查是否包含工具格式
        if '"tool"' in response and '"arguments"' in response:
            try:
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    data = json.loads(response[start:end])
                    if "tool" in data:
                        return data
            except:
                pass

        return None