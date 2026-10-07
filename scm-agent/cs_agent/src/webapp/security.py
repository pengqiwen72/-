"""
坐席工作台的登录与鉴权：签发 / 校验 JWT。

和 `auth/auth.py` 的分工
-----------------------
`auth/auth.py` 是**平台级**鉴权：`langgraph dev` 用它校验调用方身份，
配置在 `langgraph.json` 的 `auth` 段，管的是「谁能调这个图服务」。

这里是**应用级**登录：坐席在工作台页面输入账号密码，换一个 JWT，
后续每个 `/cs/*` 请求带着它。两者不冲突，也不需要共享同一份实现 ——
平台鉴权发生在网关层，页面鉴权发生在业务层。

为什么不再沿用「一个静态 token 走天下」
----------------------------------------
改造前 `/cs/get_token` 直接把 `DEMO_USER_TOKEN` 原样返回：所有人拿到同一个
令牌，`external_id` 也永远是同一个人。那套写法一旦有人照着抄进真实项目，
就是「账号 A 能看账号 B 的会话」—— 而它在 demo 里看起来完全正常。
现在按用户签发，身份写在令牌里，`exp` 由配置控制。

密钥缺失时的处理
----------------
不内置默认密钥（那等于把「所有人共用一把公开的钥匙」写进代码）。没有配置
`JWT_SECRET_KEY` 时**每次进程启动随机生成一把**：本地起服务不用配任何东西，
但重启后旧令牌失效 —— 这个副作用是刻意保留的，它会让人尽快去配一个真密钥，
而不是把不安全的默认值一路带到线上。
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Header, HTTPException

from utils.agent_logger import get_logger

logger = get_logger("webapp_security")

ALGORITHM = "HS256"

#: 未配置 JWT_SECRET_KEY 时使用的进程级随机密钥（见模块说明）
_RUNTIME_SECRET = secrets.token_urlsafe(32)
_warned = False


def secret_key() -> str:
    global _warned
    configured = (os.getenv("JWT_SECRET_KEY") or "").strip()
    if configured:
        return configured
    if not _warned:
        _warned = True
        logger.warning(
            "未配置 JWT_SECRET_KEY，已生成进程级随机密钥；"
            "服务重启后此前签发的令牌会全部失效（本地演示无妨，部署必须配置）"
        )
    return _RUNTIME_SECRET


def expire_minutes() -> int:
    raw = (os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES") or "").strip()
    try:
        return int(raw) if raw else 8 * 60
    except ValueError:
        return 8 * 60


def demo_credentials() -> tuple[str, str]:
    """内置演示账号。返回 (user_id, password)。"""
    return (
        (os.getenv("DEMO_USER_ID") or "CS001").strip(),
        (os.getenv("DEMO_USER_PASSWORD") or "cs-demo-2026").strip(),
    )


def create_access_token(user_id: str, external_id: str = "") -> str:
    """签发坐席令牌。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "external_id": external_id,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes()),
    }
    return jwt.encode(payload, secret_key(), algorithm=ALGORITHM)


@dataclass(frozen=True)
class AgentIdentity:
    """当前登录的坐席。"""

    user_id: str
    external_id: str = ""


def decode_token(token: str) -> AgentIdentity:
    """校验令牌并取出身份。任何问题都抛 401，不泄漏具体原因。"""
    try:
        payload = jwt.decode(token, secret_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="令牌无效")

    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="令牌无效")
    return AgentIdentity(user_id=user_id, external_id=str(payload.get("external_id") or ""))


async def require_agent(authorization: Optional[str] = Header(default=None)) -> AgentIdentity:
    """FastAPI 依赖：要求请求方是已登录坐席。

    `Authorization` 用 Header 自动注入，OpenAPI 里也就长出了鉴权入口，
    不然调接口的人只能靠翻文档才知道要带 token。
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization 头")

    # split() 不带参数：容忍多余空白；带参数则要求恰好两段
    parts = authorization.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Authorization 头格式应为 'Bearer <token>'")

    return decode_token(parts[1])
