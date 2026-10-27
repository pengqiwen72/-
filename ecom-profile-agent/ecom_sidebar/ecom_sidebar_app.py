"""
Ecom Agent Web API
基于 FastAPI 的 Web API 服务，用于与 ecom Agent 进行交互
"""
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import json
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from dotenv import load_dotenv
from ecom_agent_api.ecom_agent_api import EcomAgentAPI

load_dotenv()


app = FastAPI(
    title="Ecom Agent Web API",
    description="Ecom Agent Web API 服务",
    version="1.0.0"
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该限制具体的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/ecom_web", StaticFiles(directory="./ecom_web"), name="ecom_web")

# 安全认证
security = HTTPBearer()

# 全局变量存储客户端连接
clients: Dict[str, EcomAgentAPI] = {}

# SAGT配置
ECOM_SERVER_URL = os.getenv("ECOM_SERVER_URL")
ECOM_GRAPH_ID = os.getenv("ECOM_GRAPH_ID")
ECOM_USER_ID=os.getenv("ECOM_USER_ID")
ECOM_PASSWORD=os.getenv("ECOM_PASSWORD")

# Web配置
WEB_USER_ID=os.getenv("WEB_USER_ID")
WEB_PASSWORD=os.getenv("WEB_PASSWORD")

# JWT配置
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# 业务DEMO配置
EXTERNAL_ID = os.getenv("EXTERNAL_ID")

# 请求模型
class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")

class SendMessageRequest(BaseModel):
    message: Optional[str] = Field(None, description="消息内容")
    menu_id: Optional[str] = Field(None, description="菜单ID，如果是点击菜单触发")

class InterruptConfirmRequest(BaseModel):
    confirmed: str = Field(..., description="确认结果: ok, discard, recreate")

# 响应模型
class LoginResponse(BaseModel):
    success: bool = Field(..., description="登录是否成功")
    token: str = Field(default="", description="访问令牌")
    message: str = Field(default="", description="响应消息")

class TaskResult(BaseModel):
    task_result: str = Field(default="", description="任务结果")
    task_result_explain: str = Field(default="", description="任务结果解释")
    task_result_code: int = Field(default=1, description="任务结果代码，0: 结果有效，1: 结果无效")

class NodeResult(BaseModel):
    execute_node_name: str = Field(default="", description="节点名称")
    execute_result_code: int = Field(default=1, description="节点执行结果代码，0: 成功，1: 失败")
    execute_result_msg: str = Field(default="", description="节点执行结果消息")
    execute_exceptions: List[str] = Field(default=[], description="节点执行异常信息")

class InterruptInfo(BaseModel):
    description: str = Field(..., description="中断描述")
    data: Dict[str, Any] = Field(..., description="中断数据")

# JWT工具函数
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建JWT访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> dict:
    """验证JWT令牌"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的访问令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

# 工具函数
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """获取当前用户"""
    token = credentials.credentials
    payload = verify_token(token)
    username = payload.get("sub")
    
    # 检查是否有对应的SAGT客户端连接
    if token not in clients:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="会话已过期，请重新登录"
        )
    return token, username

async def get_client(token: str) -> EcomAgentAPI:
    """获取客户端实例"""
    if token not in clients:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="客户端未连接"
        )
    return clients[token]

# API 路由
@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """用户登录 - 使用独立的Web账号验证"""
    try:
        # 验证Web服务账号（不是SAGT账号）
        if request.username != WEB_USER_ID or request.password != WEB_PASSWORD:
            return LoginResponse(
                success=False,
                message="用户名或密码错误"
            )
        
        # Web账号验证成功后，创建SAGT客户端实例并使用SAGT账号连接
        client = EcomAgentAPI()
        
        # 使用配置的SAGT账号连接到SAGT服务器
        success = await client.connect(
            ecom_server_url=ECOM_SERVER_URL,
            ecom_user=ECOM_USER_ID,
            password=ECOM_PASSWORD
        )
        
        if success:
            # 生成JWT令牌
            access_token_expires = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": request.username},
                expires_delta=access_token_expires
            )
            clients[access_token] = client
            
            return LoginResponse(
                success=True,
                token=access_token,
                message="登录成功"
            )
        else:
            return LoginResponse(
                success=False,
                message="后端服务连接失败，请稍后重试"
            )
            
    except Exception as e:
        return LoginResponse(
            success=False,
            message=f"登录失败: {str(e)}"
        )

@app.post("/api/logout")
async def logout(user_info = Depends(get_current_user)):
    """用户登出"""
    try:
        token, username = user_info
        if token in clients:
            await clients[token].disconnect()
            del clients[token]
        return {"success": True, "message": "登出成功"}
    except Exception as e:
        return {"success": False, "message": f"登出失败: {str(e)}"}

@app.post("/api/send_message")
async def send_message(request: SendMessageRequest, user_info = Depends(get_current_user)):
    """发送消息给 Agent（流式响应）"""
    try:
        token, username = user_info
        client = await get_client(token)
        
        # 创建助手和线程（使用Web用户名作为用户标识）
        web_user_id = username  # 从JWT中获取用户名
        assistant_id = await client.create_assistant(
            graph_id=ECOM_GRAPH_ID,
            external_id=EXTERNAL_ID,
            user_id=web_user_id
        )
        
        thread_id = await client.create_thread(
            user_id=web_user_id,
            external_id=EXTERNAL_ID
        )
        
        # 准备输入数据
        # task_input 承担两种职责：指令名称、talk的内容
        # agent会自动区分/识别是不是指令，如果不是指令，自动进入咨询/talk流程
        if request.menu_id:
            # 如果是菜单点击，使用菜单ID作为输入
            input_data = {"task_input": request.menu_id}
        elif request.message:
            # 如果是用户输入，直接使用消息内容
            input_data = {"task_input": request.message}
        else:
            # 如果两者都没有，返回错误
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="必须提供消息内容或菜单ID"
            )
        
        # 创建流式运行
        stream = await client.create_stream_run(
            thread_id=thread_id,
            assistant_id=assistant_id,
            input=input_data
        )
        
        async def generate_stream():
            """生成流式响应"""
            try:
                async for chunk in stream:
                    # 将 chunk 转换为 JSON 字符串并发送
                    chunk_data = {
                        "event": getattr(chunk, 'event', 'unknown'),
                        "data": getattr(chunk, 'data', {})
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_data = {
                    "event": "error",
                    "data": {"error": str(e)}
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache"}
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"发送消息失败: {str(e)}"
        )

@app.get("/api/get_interrupt")
async def get_interrupt(user_info = Depends(get_current_user)):
    """获取中断信息"""
    try:
        token, username = user_info
        client = await get_client(token)
        
        web_user_id = username  # 从JWT中获取用户名
        thread_id = await client.get_thread_id(
            user_id=web_user_id,
            external_id=EXTERNAL_ID
        )
        
        if not thread_id:
            return {"has_interrupt": False, "interrupt_info": None}
        
        # 检查是否有中断
        has_interrupt = await client.has_interrupts(thread_id)
        
        if has_interrupt:
            interrupts = await client.get_interrupts_from_thread(thread_id)
            return {
                "has_interrupt": True,
                "interrupt_info": interrupts
            }
        else:
            return {"has_interrupt": False, "interrupt_info": None}
            
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取中断信息失败: {str(e)}"
        )

@app.post("/api/confirm_interrupt")
async def confirm_interrupt(request: InterruptConfirmRequest, user_info = Depends(get_current_user)):
    """确认中断（流式响应）"""
    try:
        token, username = user_info
        client = await get_client(token)
        
        web_user_id = username  # 从JWT中获取用户名
        assistant_id = await client.create_assistant(
            graph_id=ECOM_GRAPH_ID,
            external_id=EXTERNAL_ID,
            user_id=web_user_id
        )
        
        thread_id = await client.get_thread_id(
            user_id=web_user_id,
            external_id=EXTERNAL_ID
        )
        
        # 准备确认命令
        confirmed = {"confirmed": request.confirmed}
        command = {"resume": confirmed}
        
        # 恢复中断运行
        stream = await client.resume_interrupt_run(
            thread_id=thread_id,
            assistant_id=assistant_id,
            command=command
        )
        
        async def generate_stream():
            """生成流式响应"""
            try:
                async for chunk in stream:
                    chunk_data = {
                        "event": getattr(chunk, 'event', 'unknown'),
                        "data": getattr(chunk, 'data', {})
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_data = {
                    "event": "error",
                    "data": {"error": str(e)}
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache"}
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"确认中断失败: {str(e)}"
        )

@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "message": "Ecom Agent Web API 运行正常"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
