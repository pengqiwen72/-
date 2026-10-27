from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from utils.agent_logger import get_logger
import os
from dotenv import load_dotenv

load_dotenv()

logger = get_logger("webapp")

# 生命周期管理器

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application...")
    yield
    logger.info("Shutting down application...")

app = FastAPI(lifespan=lifespan)

# 中间件

class HeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger.info("custom header for request url: %s", request.url)
        
        response = await call_next(request)
        response.headers["X-custom-header"] = "by Sagt Agent"
        return response

app.add_middleware(HeaderMiddleware)


# 路由
@app.get("/sagt/health")
async def health():
    logger.info("sagt health check")
    return {"status": "ok"}

@app.post("/sagt/get_token")
async def get_token(request: Request):
    logger.info("ecom get_token")
    body = await request.json()
    password = body.get("password")
    user_id = body.get("user_id")

    if not user_id or not password:
        return Response(status_code=403)
    # 去数据库根据用户名user_id去得到该用户对象，然后比较用户对象的密码是否和password一样
    demo_user_id = os.getenv("DEMO_USER_ID")# 正确的用户名
    demo_password = os.getenv("DEMO_USER_PASSWORD")# 正确的密码

    if user_id == demo_user_id and password == demo_password:
        logger.info("ecom get_token success: %s", user_id)
        return {
            "status": "ok",
            "token": os.getenv("DEMO_USER_TOKEN"),
            "user_id": os.getenv("DEMO_USER_ID"),
            "external_id": os.getenv("DEMO_USER_EXTERNAL_ID")
        }
    else:
        logger.info("ecom get_token failed: %s", user_id)
        return Response(status_code=403)