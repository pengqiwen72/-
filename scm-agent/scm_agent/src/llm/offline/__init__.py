"""
离线合成器包。

导入本包会触发所有合成器模块的 side-effect 注册（@synthesizer 装饰器）。
新增合成器模块时，记得在这里补一行 import。
"""

from llm.offline import prompt_view  # noqa: F401
from llm.offline import registry  # noqa: F401
from llm.offline import lexicons  # noqa: F401

# 业务合成器：import 即注册
from llm.offline import synth_core  # noqa: F401
from llm.offline import synth_scm  # noqa: F401

__all__ = [
    "registry",
    "prompt_view",
    "lexicons",
    "synth_core",
    "synth_scm",
]
