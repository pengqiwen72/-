from langgraph_sdk import Auth
from dotenv import load_dotenv
from utils.agent_logger import get_logger
import os
from fastapi import Request

load_dotenv()

logger = get_logger("auth")

def _build_valid_tokens() -> dict:
    """构造 token -> 身份 的映射。

    ⚠️ 必须在 DEMO_USER_TOKEN 缺失时返回空表，而不是写进一个 `None` 键：
    否则 `token in VALID_TOKENS` 会变成「谁是 None 谁通过」，语义完全错位。
    """
    token = os.getenv("DEMO_USER_TOKEN")
    if not token:
        logger.warning("未配置 DEMO_USER_TOKEN，鉴权表为空（所有请求都会 401）")
        return {}
    return {
        token: {
            "user_id": os.getenv("DEMO_USER_ID", ""),
            "external_id": os.getenv("DEMO_USER_EXTERNAL_ID", ""),
        }
    }


VALID_TOKENS = _build_valid_tokens()

auth = Auth()

@auth.on
async def auth_on(ctx: Auth.types.AuthContext, value: dict):
    logger.info("auth_on ctx: %s", ctx)
    logger.info("auth_on value: %s", value)
    return True

async def verify_token(token: str) -> bool:
    """Verify if the token is valid."""
    return token in VALID_TOKENS


@auth.authenticate
async def authenticate(request: Request, authorization: str|None) -> str:
    """校验 token，并返回该 token 对应的用户身份。"""
    if not authorization:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    # split() 不带参数：容忍多余空白；带参数则要求恰好两段
    parts = authorization.split()
    if len(parts) != 2:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    scheme, token = parts
    if scheme != "Bearer":
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    if not await verify_token(token):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    # ⚠️ 这里曾经硬编码返回 "CS001"，导致任何合法 token 都被当成同一个人，
    #    下游按 user_id 取数据全部串号。身份必须从 token 映射里取。
    identity = VALID_TOKENS[token].get("user_id") or ""
    logger.info("authenticate ok, identity=%s", identity)
    return identity