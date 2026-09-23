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


async def _identity_from_jwt(token: str) -> str | None:
    """把 Bearer 当成坐席 JWT 试解一次（密钥与 `/cs/*` 接口同一套）。

    解析失败只说明「这不是一个有效的 JWT」，返回 None 交给静态令牌那条路 ——
    **不能在这里直接 401**：`DEMO_USER_TOKEN` 本来就不是 JWT，先判 JWT 再
    回落静态表，顺序反了会把静态令牌全部拒掉。
    """
    try:
        from webapp.security import decode_token

        return decode_token(token).user_id or None
    except Exception:
        return None


@auth.authenticate
async def authenticate(request: Request, authorization: str|None) -> str:
    """校验 token，并返回该 token 对应的用户身份。

    接受两种 Bearer：

    * **坐席 JWT** —— 工作台登录换来的那个，与 `/cs/*` 接口共用一套密钥。
      这样「页面登录 → 调平台接口」是一条链路，不用再单独配一个平台令牌。
    * **静态 `DEMO_USER_TOKEN`** —— 给 `langgraph dev` / Studio 用：直接起平台
      调试时不想先走一遍登录，留一个固定令牌最省事。

    ⚠️ 跨进程时 JWT 必须配 `JWT_SECRET_KEY`：不配的话每个进程各自随机生成一把
    （见 `webapp/security.py`），平台和 webapp 分开跑就互不认账。
    """
    if not authorization:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    # split() 不带参数：容忍多余空白；带参数则要求恰好两段
    parts = authorization.split()
    if len(parts) != 2:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    scheme, token = parts
    if scheme != "Bearer":
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    identity = await _identity_from_jwt(token)
    if identity:
        logger.info("authenticate ok（坐席 JWT）, identity=%s", identity)
        return identity

    # ⚠️ 这里曾经硬编码返回 "CS001"，导致任何合法 token 都被当成同一个人，
    #    下游按 user_id 取数据全部串号。身份必须从 token 映射里取。
    if token in VALID_TOKENS:
        identity = VALID_TOKENS[token].get("user_id") or ""
        logger.info("authenticate ok（静态令牌）, identity=%s", identity)
        return identity

    raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")