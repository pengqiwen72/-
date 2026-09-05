"""
工具函数模块：日志追踪、异常捕获、通用工具
"""

import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from functools import wraps


class Logger:
    """统一日志记录器 - 带颜色和结构化输出"""

    COLORS = {
        "INFO": "\033[94m",    # 蓝色
        "WARN": "\033[93m",    # 黄色
        "ERROR": "\033[91m",   # 红色
        "DEBUG": "\033[90m",   # 灰色
        "TOOL": "\033[92m",    # 绿色
        "RESET": "\033[0m"
    }

    def __init__(self, name: str = "Agent"):
        self.name = name
        self.logs: List[Dict] = []

    def _log(self, level: str, msg: str, data: Optional[Dict] = None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color = self.COLORS.get(level, "")

        entry = {
            "timestamp": timestamp,
            "level": level,
            "message": msg,
            "data": data or {}
        }
        self.logs.append(entry)

        # 格式化输出
        print(f"{color}[{timestamp}] [{level}] {msg}{self.COLORS['RESET']}")
        if data:
            print(f"  └─ {json.dumps(data, ensure_ascii=False, indent=2)}")

    def info(self, msg: str, data: Optional[Dict] = None):
        self._log("INFO", msg, data)

    def warn(self, msg: str, data: Optional[Dict] = None):
        self._log("WARN", msg, data)

    def error(self, msg: str, data: Optional[Dict] = None):
        self._log("ERROR", msg, data)

    def debug(self, msg: str, data: Optional[Dict] = None):
        self._log("DEBUG", msg, data)

    def tool(self, msg: str, data: Optional[Dict] = None):
        self._log("TOOL", msg, data)

    def get_logs(self) -> List[Dict]:
        return self.logs


def safe_json_parse(text: str) -> Dict:
    """安全解析 JSON，失败返回空字典"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def extract_json(text: str) -> Optional[Dict]:
    """从文本中提取 JSON 对象"""
    try:
        # 找第一个 { 和最后一个 }
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except:
        pass
    return None


def timeit(func):
    """装饰器：记录执行时间"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.debug(f"{func.__name__} 耗时", {"elapsed": f"{elapsed:.3f}s"})
        return result
    return wrapper


# 全局日志实例
logger = Logger()