from pydantic import BaseModel
import json

class EcomBaseModel(BaseModel):
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

    def model_dump_json(self) -> str:
        """获取格式化的 JSON"""
        return json.dumps(self.model_dump(), indent=2, ensure_ascii=False)