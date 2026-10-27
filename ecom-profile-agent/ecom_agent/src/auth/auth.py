from langgraph_sdk import Auth
from dotenv import load_dotenv
from utils.agent_logger import get_logger
import os
from fastapi import Request

load_dotenv()

logger = get_logger("auth")

VALID_TOKENS = {
    os.getenv("DEMO_USER_TOKEN"): {"user_id": os.getenv("DEMO_USER_ID"), "external_id": os.getenv("DEMO_USER_EXTERNAL_ID")},
}

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
    """Check if the user's token is valid."""
    logger.info("authenticate request: %s", request)
    logger.info("authenticate authorization: %s", authorization)

    path = request.url.path
    logger.info("authenticate path: %s", path)

    method = request.method
    logger.info("authenticate method: %s", method)

    path_params = request.path_params
    logger.info("authenticate path_params: %s", path_params)

    query_params = request.query_params
    logger.info("authenticate query_params: %s", query_params)

    headers = request.headers
    logger.info("authenticate headers: %s", headers)

    if not authorization:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    scheme, token = authorization.split()
    if scheme != "Bearer":
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")
    
    verified = await verify_token(token)
    
    if not verified:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    return "THOR"