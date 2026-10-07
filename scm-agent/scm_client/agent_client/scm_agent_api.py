from typing import Dict, List, Optional, Any
from langgraph_sdk import get_client
from langgraph_sdk.schema import Thread, Assistant, Interrupt, Run
from langgraph.types import Command
from dotenv import load_dotenv
import requests
import uuid
import time

load_dotenv()


def generate_stable_uuid(name: str) -> str:
    """
    根据名称生成稳定的UUID
    使用UUID5算法，确保相同的名称总是生成相同的UUID
    
    Args:
        name: 名称字符串
        
    Returns:
        稳定的UUID字符串
    """
    namespace_uuid = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
    stable_uuid = uuid.uuid5(namespace_uuid, name)
    return str(stable_uuid)

class ScmAgentAPI:
    """Cs 客户端封装类"""
    
    _client = None
    _connected = False

    ################## 连接管理 ##################

    async def connect(self, scm_server_url: str, scm_user: str, password: str) -> bool:
        """
        连接到LangGraph服务器
        
        Returns:
            bool: 连接是否成功
        """
        try:
            # 获取token和其他登录信息
            response = requests.post(f"{scm_server_url}/scm/get_token", json={"user_id": scm_user, "password": password})

            if response.status_code != 200:
                self._connected = False
                return self._connected
            
            user_token = response.json().get("token")
            
            # 创建客户端连接
            headers = {"Authorization": "Bearer " + user_token}
            self._client = get_client(url = scm_server_url, headers = headers)
            
            self._connected = True
            return self._connected
            
        except Exception as e:
            self._connected = False
            return self._connected

    async def disconnect(self):
        """断开连接"""
        self._client = None
        self._connected = False

    
    async def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._client is not None
    
    ################## 助手管理 ##################
    
    async def create_assistant(self, graph_id: str, external_id : str, user_id: str) -> str:
        """
        创建新的助手，如果已经存在，返回已存在的助手ID
        
        Args:
            external_id: 外部ID
            user_id: 员工ID
            
        Returns:
            str: 助手ID
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            assistant_id_format = f"{graph_id}_{user_id}_{external_id}"
            # 生成稳定的助手ID
            assistant_id = generate_stable_uuid(assistant_id_format)

            assistant = await self._client.assistants.create(
                graph_id=graph_id,
                config={
                    "configurable": {
                        "external_id": external_id,
                        "user_id": user_id
                    }
                },
                assistant_id=assistant_id,
                if_exists="do_nothing",
                name=f"ScmAgent_{assistant_id}"
            )

            return assistant['assistant_id']
        except Exception as e:
            return ""
    
    async def delete_assistant(self, assistant_id: str) -> bool:
        """
        删除助手
        
        Args:
            assistant_id: 助手ID
            
        Returns:
            bool: 是否删除成功
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            await self._client.assistants.delete(assistant_id=assistant_id)
            return True
        except Exception as e:
            return False


    async def list_assistants(self) -> List[Assistant]:
        """
        获取可用的助手列表
        
        Returns:
            List[Dict]: 助手列表
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            assistants = await self._client.assistants.search(limit=100)
            # 确保返回列表而不是None
            return assistants
        except Exception as e:
            return []

    ################## 线程管理 ##################

    thread_name_format = "{user_id}_{external_id}"

    async def get_thread(self, user_id: str, external_id: str) -> Thread:
        """
        获取线程
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")

        try:
            thread_name = self.thread_name_format.format(user_id=user_id, external_id=external_id)
            thread_id = generate_stable_uuid(thread_name)
            thread = await self._client.threads.get(thread_id=thread_id)
            return thread
        except Exception as e:
            return None

    async def get_thread_id(self, user_id: str, external_id: str) -> str:
        """
        获取线程ID
        """
        thread_name = self.thread_name_format.format(user_id=user_id, external_id=external_id)
        thread_id = generate_stable_uuid(thread_name)
        return thread_id

    async def create_thread(self, user_id: str, external_id: str) -> Optional[str]:
        """
        创建新的对话线程
        
        Returns:
            str: 线程ID
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        thread_name = self.thread_name_format.format(user_id=user_id, external_id=external_id)
        thread_id = generate_stable_uuid(thread_name)

        try:
            thread = await self._client.threads.create(thread_id=thread_id, if_exists="do_nothing")
            return thread['thread_id']
        except Exception as e:
            return None

    async def delete_thread(self, thread_id: str) -> bool:
        """
        删除线程
        
        Args:
            thread_id: 线程ID
            
        Returns:
            bool: 是否删除成功
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            await self._client.threads.delete(thread_id=thread_id)
            return True
        except Exception as e:
            print(f"删除线程失败: {e}")
            return False


    async def list_threads(self) -> List[Dict[str, Any]]:
        """
        获取所有线程列表
        
        Returns:
            List[Dict]: 线程列表
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            threads = await self._client.threads.search(limit=100)
            return threads
        except Exception as e:
            ##console.print(f"[red]获取线程列表失败: {e}[/red]")
            return []

    async def has_interrupts(self, thread_id: str) -> bool:
        """
        检查线程是否存在中断
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        interrupts = await self.get_interrupts_from_thread(thread_id)
        
        return len(interrupts) > 0

    async def get_interrupts_from_thread(self, thread_id: str) -> List[Interrupt]:
        """
        获取线程中断信息
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            thread = await self._client.threads.get(thread_id=thread_id)
            interrupts = thread.get("interrupts", [])
            return interrupts
        except Exception as e:
            return []


    ################## 运行管理 ##################
    async def create_stream_run(self, thread_id: str, assistant_id: str, input: Dict[str, Any]):
        """
        创建新的对话运行
        
        Args:
            thread_id: 线程ID
            assistant_id: 助手ID
            
        Returns:
            Dict: 运行状态
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            run = self._client.runs.stream(thread_id=thread_id, assistant_id=assistant_id, input=input, stream_subgraphs=True)
            return run
        except Exception as e:
            return None

    async def resume_interrupt_run(self, thread_id: str, assistant_id: str, command: Command):
        """
        恢复中断运行
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        try:
            run = self._client.runs.stream(thread_id=thread_id, assistant_id=assistant_id, command=command, stream_subgraphs=True)
            return run
        except Exception as e:
            return None

    async def list_runs(self, thread_id: str) -> List[Run]:
        """
        获取所有运行列表
        """
        if not await self.is_connected():
            raise RuntimeError("客户端未连接，请先调用 connect()")
        
        runs = await self._client.runs.list(thread_id=thread_id, limit=100)
        return runs

