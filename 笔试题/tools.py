import math
import json
from typing import Dict, Any, Callable, Optional, List
from dataclasses import dataclass
from utils import logger, timeit


@dataclass
class ToolSchema:
    """工具参数 Schema"""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema


class Tool:
    """工具封装"""
    def __init__(self, schema: ToolSchema, func: Callable):
        self.schema = schema
        self.func = func
    
    @timeit
    def execute(self, **kwargs) -> str:
        """执行工具，返回字符串结果"""
        try:
            result = self.func(**kwargs)
            return str(result)
        except Exception as e:
            error_msg = f"工具执行失败: {str(e)}"
            logger.error(error_msg, {"tool": self.schema.name, "args": kwargs})
            return error_msg
    
    def to_openai_schema(self) -> Dict:
        """转 OpenAI 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.schema.name,
                "description": self.schema.description,
                "parameters": self.schema.parameters
            }
        }


class ToolRegistry:
    """工具注册中心"""
    
    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._register_all()
    
    def register(self, schema: ToolSchema):
        """注册装饰器"""
        def decorator(func: Callable):
            self._tools[schema.name] = Tool(schema, func)
            logger.info(f"注册工具: {schema.name}")
            return func
        return decorator
    
    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)
    
    def list(self) -> List[str]:
        return list(self._tools.keys())
    
    def get_schemas(self) -> List[Dict]:
        """获取所有工具 Schema（OpenAI 格式）"""
        return [t.to_openai_schema() for t in self._tools.values()]
    
    def get_description(self) -> str:
        """获取工具描述文本"""
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.schema.name}: {t.schema.description}")
            lines.append(f"  参数: {json.dumps(t.schema.parameters, ensure_ascii=False)}")
        return "\n".join(lines)
    
    def _register_all(self):
        """注册所有工具"""
        self._register_calculator()
        self._register_search()
        self._register_weather()
    
    # ========== 工具实现 ==========
    
    def _register_calculator(self):
        """1. 计算器"""
        schema = ToolSchema(
            name="calculator",
            description="执行数学计算，支持 + - * / ** sqrt sin cos tan log",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 '2 + 3 * 4' 或 'sqrt(16)'"
                    }
                },
                "required": ["expression"]
            }
        )
        
        @self.register(schema)
        def calculator(expression: str) -> float:
            safe = {
                'abs': abs, 'round': round, 'max': max, 'min': min,
                'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
                'sqrt': math.sqrt, 'log': math.log, 'exp': math.exp,
                'pi': math.pi, 'e': math.e, 'pow': pow
            }
            return eval(expression, {"__builtins__": {}}, safe)
    
    def _register_search(self):
        """2. 搜索（Mock）"""
        schema = ToolSchema(
            name="search",
            description="搜索互联网信息（模拟数据）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        )
        
        @self.register(schema)
        def search(query: str) -> str:
            # 模拟知识库
            kb = {
                "python": "Python 是 Guido 于 1991 年创建的高级编程语言。",
                "agent": "AI Agent 是能自主决策的智能系统。",
                "杭州": "杭州是浙江省省会，以西湖闻名。",
                "天气": "今天杭州晴朗，25°C，适合外出。",
                "人工智能": "AI 是让机器像人一样思考的学科。"
            }
            for k, v in kb.items():
                if k in query.lower():
                    return f"搜索结果: {v}"
            return f"关于 '{query}' 暂无搜索结果。"
    
    def _register_weather(self):
        """3. 天气（Mock）"""
        schema = ToolSchema(
            name="weather",
            description="查询城市天气（模拟数据）",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称"}
                },
                "required": ["city"]
            }
        )
        
        @self.register(schema)
        def weather(city: str) -> str:
            data = {
                "杭州": "🌤️ 晴天 25°C 湿度60% 东南风3级",
                "北京": "☀️ 晴 28°C 湿度40% 西南风2级",
                "上海": "🌧️ 小雨 22°C 湿度80% 东风4级",
                "深圳": "⛅ 多云 30°C 湿度70% 南风3级"
            }
            for k, v in data.items():
                if k in city:
                    return f"{city}天气: {v}"
            return f"{city}天气: 晴 24°C 湿度55%"


# 全局工具注册中心
tools = ToolRegistry()