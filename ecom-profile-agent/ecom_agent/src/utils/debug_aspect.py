"""
调试切面装饰器

提供通用的函数调试功能，自动打印入参和出参
"""

import functools
import inspect
import json
import time
from typing import Any, Callable, Dict, List, Optional, Union
from datetime import datetime
from pprint import pformat

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.agent_logger import get_logger


class DebugAspect:
    """调试切面类
    
    提供各种调试装饰器和工具方法
    """
    
    def __init__(self, 
                 enable: bool = True,
                 max_length: int = 500,
                 show_args: bool = True,
                 show_kwargs: bool = True,
                 show_return: bool = True,
                 show_execution_time: bool = True,
                 show_exception: bool = True,
                 indent: int = 2):
        """初始化调试切面
        
        Args:
            enable: 是否启用调试
            max_length: 参数/返回值的最大显示长度
            show_args: 是否显示位置参数
            show_kwargs: 是否显示关键字参数
            show_return: 是否显示返回值
            show_execution_time: 是否显示执行时间
            show_exception: 是否显示异常信息
            indent: 缩进空格数
        """
        self.enable = enable
        self.max_length = max_length
        self.show_args = show_args
        self.show_kwargs = show_kwargs
        self.show_return = show_return
        self.show_execution_time = show_execution_time
        self.show_exception = show_exception
        self.indent = indent
        self._call_depth = 0  # 调用深度，用于缩进
    
    def _format_value(self, value: Any, max_length: Optional[int] = None) -> str:
        """格式化值为字符串
        
        Args:
            value: 要格式化的值
            max_length: 最大长度限制
            
        Returns:
            格式化后的字符串
        """
        if max_length is None:
            max_length = self.max_length
        
        try:
            # 尝试JSON序列化（更清晰）
            if isinstance(value, (dict, list, tuple)):
                formatted = json.dumps(value, ensure_ascii=False, indent=2)
            else:
                formatted = str(value)
        except (TypeError, ValueError):
            # 如果JSON序列化失败，使用pformat
            formatted = pformat(value, width=80, depth=3)
        
        # 长度限制
        if len(formatted) > max_length:
            formatted = formatted[:max_length] + "..."
        
        return formatted
    
    def _get_function_signature(self, func: Callable) -> str:
        """获取函数签名"""
        try:
            sig = inspect.signature(func)
            return f"{func.__name__}{sig}"
        except (ValueError, TypeError):
            return f"{func.__name__}(...)"
    
    def _print_with_indent(self, message: str, extra_indent: int = 0):
        """带缩进打印消息"""
        if not self.enable:
            return
        
        # 获取logger实例
        logger = get_logger("debug_aspect")
        
        total_indent = (self._call_depth + extra_indent) * self.indent
        indent_str = " " * total_indent
        
        # 处理多行消息
        lines = message.split('\n')
        for line in lines:
            formatted_line = f"{indent_str}{line}"
            logger.info(formatted_line)
    
    def debug_function(self, 
                      prefix: str = "",
                      show_signature: bool = True,
                      custom_formatter: Optional[Callable] = None):
        """函数调试装饰器
        
        Args:
            prefix: 日志前缀
            show_signature: 是否显示函数签名
            custom_formatter: 自定义格式化函数
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self.enable:
                    return func(*args, **kwargs)
                
                # 增加调用深度
                self._call_depth += 1
                
                try:
                    # 函数开始信息
                    func_name = f"{prefix}{func.__name__}" if prefix else func.__name__
                    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    
                    self._print_with_indent(f"🔍 [{timestamp}] ➤ {func_name}")
                    
                    # 显示函数签名
                    if show_signature:
                        signature = self._get_function_signature(func)
                        self._print_with_indent(f"📝 签名: {signature}", 1)
                    
                    # 显示参数
                    if self.show_args and args:
                        self._print_with_indent("📥 位置参数:", 1)
                        for i, arg in enumerate(args):
                            formatted_arg = self._format_value(arg)
                            self._print_with_indent(f"  [{i}]: {formatted_arg}", 1)
                    
                    if self.show_kwargs and kwargs:
                        self._print_with_indent("📥 关键字参数:", 1)
                        for key, value in kwargs.items():
                            formatted_value = self._format_value(value)
                            self._print_with_indent(f"  {key}: {formatted_value}", 1)
                    
                    # 执行函数
                    start_time = time.time()
                    result = func(*args, **kwargs)
                    end_time = time.time()
                    
                    # 显示返回值
                    if self.show_return:
                        formatted_result = self._format_value(result)
                        self._print_with_indent(f"📤 返回值: {formatted_result}", 1)
                    
                    # 显示执行时间
                    if self.show_execution_time:
                        execution_time = (end_time - start_time) * 1000
                        self._print_with_indent(f"⏱️ 执行时间: {execution_time:.2f}ms", 1)
                    
                    # 函数结束信息
                    end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    self._print_with_indent(f"✅ [{end_timestamp}] ✓ {func_name}")
                    
                    return result
                    
                except Exception as e:
                    # 显示异常信息
                    if self.show_exception:
                        error_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        self._print_with_indent(f"❌ [{error_timestamp}] ✗ {func_name}", 0)
                        self._print_with_indent(f"🚨 异常: {type(e).__name__}: {str(e)}", 1)
                    
                    raise
                    
                finally:
                    # 减少调用深度
                    self._call_depth -= 1
            
            return wrapper
        return decorator
    
    def debug_class(self, 
                   include_private: bool = False,
                   include_magic: bool = False,
                   exclude_methods: Optional[List[str]] = None):
        """类调试装饰器
        
        Args:
            include_private: 是否包含私有方法
            include_magic: 是否包含魔术方法
            exclude_methods: 排除的方法列表
        """
        exclude_methods = exclude_methods or []
        
        def decorator(cls):
            for attr_name in dir(cls):
                attr = getattr(cls, attr_name)
                
                # 跳过非方法属性
                if not callable(attr):
                    continue
                
                # 跳过排除的方法
                if attr_name in exclude_methods:
                    continue
                
                # 跳过私有方法（除非明确包含）
                if attr_name.startswith('_') and not include_private:
                    # 魔术方法特殊处理
                    if attr_name.startswith('__') and attr_name.endswith('__'):
                        if not include_magic:
                            continue
                    else:
                        continue
                
                # 应用调试装饰器
                debug_method = self.debug_function(
                    prefix=f"{cls.__name__}.",
                    show_signature=True
                )(attr)
                
                setattr(cls, attr_name, debug_method)
            
            return cls
        return decorator


# ========== 预定义的调试实例 ==========

# 默认调试器
default_debug = DebugAspect()

# 简化调试器（只显示函数名和返回值）
simple_debug = DebugAspect(
    show_args=False,
    show_kwargs=False,
    show_execution_time=False,
    max_length=100
)

# 详细调试器（显示所有信息）
verbose_debug = DebugAspect(
    max_length=1000,
    show_args=True,
    show_kwargs=True,
    show_return=True,
    show_execution_time=True,
    show_exception=True
)

# 性能调试器（主要关注执行时间）
performance_debug = DebugAspect(
    show_args=False,
    show_kwargs=False,
    show_return=False,
    show_execution_time=True,
    max_length=50
)


# ========== 便捷装饰器 ==========

def debug(func: Callable = None, *, 
         enable: bool = True,
         show_args: bool = True,
         show_return: bool = True,
         max_length: int = 300):
    """便捷的调试装饰器
    
    使用方式：
    @debug
    def my_function():
        pass
    
    或者：
    @debug(show_args=False, max_length=100)
    def my_function():
        pass
    """
    def decorator(f):
        aspect = DebugAspect(
            enable=enable,
            show_args=show_args,
            show_return=show_return,
            max_length=max_length
        )
        return aspect.debug_function()(f)
    
    if func is None:
        # 被当作带参数的装饰器使用
        return decorator
    else:
        # 被当作无参数的装饰器使用
        return decorator(func)


def debug_class(cls=None, *, 
               include_private: bool = False,
               exclude_methods: Optional[List[str]] = None):
    """便捷的类调试装饰器"""
    def decorator(c):
        aspect = DebugAspect()
        return aspect.debug_class(
            include_private=include_private,
            exclude_methods=exclude_methods
        )(c)
    
    if cls is None:
        return decorator
    else:
        return decorator(cls)


# ========== 上下文管理器 ==========

class DebugContext:
    """调试上下文管理器"""
    
    def __init__(self, name: str, debug_aspect: Optional[DebugAspect] = None):
        self.name = name
        self.aspect = debug_aspect or default_debug
        self.start_time = None
    
    def __enter__(self):
        if self.aspect.enable:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.aspect._print_with_indent(f"🎯 [{timestamp}] 开始: {self.name}")
            self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.aspect.enable:
            end_time = time.time()
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            if exc_type is None:
                # 正常结束
                if self.start_time:
                    duration = (end_time - self.start_time) * 1000
                    self.aspect._print_with_indent(
                        f"✅ [{timestamp}] 完成: {self.name} ({duration:.2f}ms)"
                    )
                else:
                    self.aspect._print_with_indent(f"✅ [{timestamp}] 完成: {self.name}")
            else:
                # 异常结束
                self.aspect._print_with_indent(
                    f"❌ [{timestamp}] 异常: {self.name} - {exc_type.__name__}: {exc_val}"
                )


def debug_context(name: str, debug_aspect: Optional[DebugAspect] = None):
    """创建调试上下文管理器的便捷函数"""
    return DebugContext(name, debug_aspect)


# ========== 使用示例 ==========

if __name__ == "__main__":
    # 示例1: 基本函数调试
    @debug
    def example_function(x: int, y: str = "default") -> dict:
        """示例函数"""
        time.sleep(0.1)  # 模拟耗时操作
        return {"result": x * 2, "message": f"Hello {y}"}
    
    # 示例2: 类调试
    @debug_class(exclude_methods=["__init__"])
    class ExampleClass:
        def __init__(self, name: str):
            self.name = name
        
        def method1(self, value: int) -> str:
            return f"{self.name}: {value}"
        
        def method2(self, data: dict) -> list:
            return list(data.keys())
    
    # 示例3: 上下文管理器
    def demo_context():
        with debug_context("数据处理流程"):
            time.sleep(0.05)
            with debug_context("子任务1"):
                time.sleep(0.02)
            with debug_context("子任务2"):
                time.sleep(0.03)
    
    # 使用logger输出演示信息
    logger = get_logger("debug_aspect")
    logger.info("🚀 调试切面演示")
    logger.info("=" * 50)
    
    # 测试函数调试
    result = example_function(5, "World")
    
    # 测试类调试
    obj = ExampleClass("测试对象")
    obj.method1(42)
    obj.method2({"a": 1, "b": 2})
    
    # 测试上下文管理器
    demo_context()
    
    logger.info("✅ 演示完成！")
