from pydantic import BaseModel
from typing import Optional
import json

class CsBaseModel(BaseModel):
    """
    自定义的 BaseModel 基类，提供通用的 schema 和示例方法
    """
    
    @classmethod
    def get_schema_json(cls) -> str:
        """获取格式化的 JSON Schema"""
        return json.dumps(cls.model_json_schema(), indent=2, ensure_ascii=False)
    
    @classmethod
    def get_example_instance(cls):
        """
        获取示例实例，子类需要重写此方法来提供具体的示例数据
        默认返回所有字段为默认值的实例
        """
        return cls()
    
    @classmethod
    def get_example_json(cls) -> str:
        """获取格式化的示例 JSON"""
        example = cls.get_example_instance()
        return json.dumps(example.model_dump(), indent=2, ensure_ascii=False)

    def model_dump_json(self, *, indent: Optional[int] = 2, **kwargs) -> str:
        """获取格式化的 JSON（默认缩进 2 空格，中文不转义）

        ⚠️ 这里必须显式接收并转发 `**kwargs`。pydantic 原签名带
        `indent` / `include` / `exclude` / `by_alias` 等参数，早期实现把它们
        整个吞掉了 —— 调用方写 `model_dump_json(indent=2)` 会直接 TypeError，
        而且报错信息完全看不出是这层覆盖导致的。
        """
        return super().model_dump_json(indent=indent, **kwargs)