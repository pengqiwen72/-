"""
对象存储 mock（OSS 风格）：文本对象落本地目录，返回伪 URL。

落盘位置
--------
`data/runtime/objects/{yyyy-MM-dd}/{name}` —— 目录可用 `MOCK_OBJECT_DIR` 覆盖
（测试指到临时目录，避免往仓库里堆产物）；URL 前缀用 `MOCK_OBJECT_BASE`
（默认 `oss://scm-demo`）。URL 是**演示用伪地址**，点不开，但形状与真实 OSS
一致，回执里带得出去、可回查、可 grep。

为什么文件路径用词法 parents 而不是 cwd
--------------------------------------
MCP 网关是子进程，它的 cwd 由 bridge 决定而不是我们挑的；`data/` 必须按
**本文件的位置**定位（与 `seed_source.py` 同一套写法），否则
`TOOL_GATEWAY=mcp` 时报告会落到别的地方 —— 而这条路径错误只在某一条运行
路径上出现。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict

from integrations.mock import chaos
from integrations.ports import FilePort
from utils.agent_logger import get_logger

logger = get_logger("file_mock")

_TZ_CHINA = timezone(timedelta(hours=8))

# src/integrations/mock/file_mock.py -> scm_agent/data/runtime/objects
_DEFAULT_DIR = Path(__file__).parents[3] / "data" / "runtime" / "objects"


def _safe_parts(name: str) -> list[str]:
    """把对象名拆成安全的路径段：挡掉绝对路径与 `..` 穿越。"""
    return [
        p for p in PurePosixPath((name or "").replace("\\", "/")).parts
        if p not in ("", ".", "..", "/")
    ]


class MockFilePort(FilePort):
    """本地目录冒充对象存储：写入即落盘，返回 oss:// 伪 URL。"""

    def put_object(self, name: str, content: str,
                   content_type: str = "text/plain") -> Dict[str, Any]:
        chaos.apply("file")

        parts = _safe_parts(name)
        if not parts:
            raise ValueError("对象名为空，无法写入对象存储")

        day = datetime.now(_TZ_CHINA).strftime("%Y-%m-%d")
        root = Path(os.getenv("MOCK_OBJECT_DIR") or _DEFAULT_DIR) / day
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        key = f"{day}/{'/'.join(parts)}"
        base = (os.getenv("MOCK_OBJECT_BASE") or "oss://scm-demo").rstrip("/")
        logger.info(f"[MOCK OSS] 已写入对象 {key}（{len(content.encode('utf-8'))} 字节）")
        return {
            "objectKey": key,
            "url": f"{base}/{key}",
            "size": len(content.encode("utf-8")),
        }
