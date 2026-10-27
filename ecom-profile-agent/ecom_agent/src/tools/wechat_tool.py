## 设置客户标签
## 设置日程
## 自建应用消息通知（人工确认机制）
## 

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import requests
from dotenv import load_dotenv
from typing import List
from utils.agent_logger import get_logger
from utils.datetime_string import datetime2timestamp
from utils.debug_aspect import debug
import threading
from typing import Optional

# 加载环境变量
load_dotenv()

logger = get_logger("wechat_tool")

class WxWorkAPI:
    """企业微信API服务类"""
    
    _instance: Optional['WxWorkAPI'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance


    def __init__(self):
        """初始化WxWorkAPI"""
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.corp_id = os.getenv('WXWORK_CORP_ID')
        self.app_id = os.getenv('WXWORK_APP_ID')
        self.app_secret = os.getenv('WXWORK_APP_SECRET')
        self.access_token = None
        self.token_expires_time = 0
        
        if not self.corp_id or not self.app_id or not self.app_secret:
            raise ValueError("请在环境变量中设置WXWORK_CORP_ID和WXWORK_APP_SECRET")
    
    def get_access_token(self):
        """获取access_token"""
        # 检查token是否过期（提前5分钟刷新）
        if self.access_token and time.time() < (self.token_expires_time - 300):
            return self.access_token
        
        # 使用企业微信access_token接口
        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        params = {
            'corpid': self.corp_id,
            'corpsecret': self.app_secret
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('errcode') == 0:
                self.access_token = data['access_token']
                self.token_expires_time = time.time() + data.get('expires_in', 7200)
                logger.info(f"获取access_token成功，有效期：{data.get('expires_in', 7200)}秒")
                return self.access_token
            else:
                errmsg = data.get('errmsg', '未知错误')
                errcode = data.get('errcode', 'unknown')
                error_msg = f"获取access_token失败: {errmsg} (错误码: {errcode})"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"请求access_token失败: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)
    
    @debug
    def create_schedule(self, user_id: str, title: str, start_time: str, duration_minutes: int = 30) -> bool: 
        """创建日程
        
        Args:
            schedule (dict): 日程信息
            
        Returns:
            bool: 创建日程是否成功
        """

        if not user_id or not title or not start_time:
            raise ValueError("user_id, title, start_time不能为空")

        start_time_timestamp = datetime2timestamp(start_time)

        end_time_timestamp = start_time_timestamp + duration_minutes * 60



        # 获取access_token
        access_token = self.get_access_token()
        
        url = f"https://qyapi.weixin.qq.com/cgi-bin/oa/schedule/add"
        params = {
            'access_token': access_token
        }

        ''' 日程数据格式示例
        {
            "schedule": {
                "admins": [
                    "ChengJianZhang"
                ],
                "summary": "告知客户白酒到货",
                "start_time": 1752836258,
                "end_time": 1752839258,
                "attendees": [
                    {
                        "userid": "ChengJianZhang"
                    }
                ]
            }
        }
        '''
        data = {
            'schedule': {
                'admins': [user_id],
                'summary': title,
                'start_time': start_time_timestamp,
                'end_time': end_time_timestamp,
                'attendees': [
                    {
                        'userid': user_id
                    }
                ]
            }
        }
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.post(url, params=params, json=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('errcode') == 0:
                # 只返回日程ID信息
                return True
            else:
                errmsg = result.get('errmsg', '未知错误')
                errcode = result.get('errcode', 'unknown')
                error_msg = f"创建日程失败: {errmsg} (错误码: {errcode})"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"请求创建日程失败: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    @debug
    def notify_user(self, user_id: str, content: str, title: str = 'Sagt 操作确认', msgtype: str = 'text') -> bool:
        """通知指定员工

        Args:
            user_id (str): 员工ID
            title (str): 通知标题
            content (str): 通知内容

        Returns:
            bool: 通知是否成功
        """

        if not user_id or not content:
            raise ValueError("user_id, content不能为空")

        if msgtype not in ['text', 'textcard']:
            raise ValueError("msgtype must be 'text' or 'textcard'")

        access_token = self.get_access_token()

        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send"

        params = {
            'access_token': access_token,
            'debug': 1
        }

        '''  消息体数据格式示例
        文本类型
        {
            "touser": "ChengJianZhang",
            "msgtype": "text",
            "agentid": 1000004,
            "text": {
                "content": "我为您创建的日程需您确认，您可以打开应用查看确认。"
            },
            "safe": 1,
            "enable_id_trans": 1,
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 300
        }

        卡片类型
        {
            "touser": "ChengJianZhang",
            "msgtype": "textcard",
            "agentid": 1000004,
            "textcard": {
                "title": "Sagt 操作确认",
                "description": "<div class=\"gray\">2025年08月26日</div> <div class=\"normal\">我为您创建的日程需您确认，您可以打开应用查看。</div><div class=\"highlight\">请及时确认</div>",
                "url": "URL",
                "btntxt": "详情"
            },
            "enable_id_trans": 1,
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 300
        }
        '''

        data_text = {
            'touser': user_id,
            'msgtype': 'text',
            'agentid': self.app_id,
            'text': {
                'content': content
            },
            'safe': 1,
            'enable_id_trans': 1,
            'enable_duplicate_check': 1,
            'duplicate_check_interval': 300
        }

        data_card = {
            'touser': user_id,
            'msgtype': 'textcard',
            'agentid': self.app_id,
            'textcard': {
                'title': title,
                'description': content,
                'url': 'URL'
            },
            'enable_id_trans': 1,
            'enable_duplicate_check': 1,
            'duplicate_check_interval': 300
        }

        if msgtype == 'text':
            data = data_text
        elif msgtype == 'textcard':
            data = data_card
        else:
            logger.error(f"Invalid msgtype: {msgtype}")
            raise ValueError("msgtype must be 'text' or 'textcard'")

        headers = {
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(url, params=params, json=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('errcode') == 0:
                # 只返回通知ID信息
                return True
            else:
                errcode = result.get('errcode', 'unknown')
                errmsg = result.get('errmsg', '未知错误')
                error_msg = f"发送通知失败: {errmsg} (错误码: {errcode})"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"请求发送通知失败: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    @debug
    def update_customer_tag(self, user_id: str, external_id: str, tag_ids_add: List[str] = [], tag_ids_remove: List[str] = []) -> bool:
        """更新指定员工的客户标签， tag_ids_add和tag_ids_remove 都为空时返回成功。
        
        Args:
            user_id (str): 员工ID
            external_id (str): 外部联系人ID
            tag_ids_add (List[str]): 需要添加的标签ID列表
            tag_ids_remove (List[str]): 需要删除的标签ID列表
            
        Returns:
            bool: 更新是否成功
        """

        if not user_id or not external_id:
            logger.error("user_id, external_id不能为空")
            raise ValueError("user_id, external_id不能为空")

        if not tag_ids_add and not tag_ids_remove:
            logger.error("tag_ids_add和tag_ids_removed都为空，默认返回成功")
            raise ValueError("tag_ids_add和tag_ids_removed都为空，默认返回成功")

        access_token = self.get_access_token()
        
        url = f"https://qyapi.weixin.qq.com/cgi-bin/externalcontact/mark_tag"
        params = {
            'access_token': access_token
        }
        
        ''' 结构体示例
        {
            "userid": "zhangsan",
            "external_userid": "woAJ2GCAAAd1NPGHKSD4wKmE8Aabj9AAA",
            "add_tag": ["TAGID1","TAGID2"],
            "remove_tag": ["TAGID3","TAGID4"]
        }
        '''

        data = {
            'userid': user_id,
            'external_userid': external_id,
            'add_tag': tag_ids_add,
            'remove_tag': tag_ids_remove
        }
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.post(url, params=params, json=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('errcode') == 0:
                # 只返回客户标签ID信息
                return True
            else:
                errmsg = result.get('errmsg', '未知错误')
                errcode = result.get('errcode', 'unknown')
                error_msg = f"更新客户标签失败: {errmsg} (错误码: {errcode})"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"请求更新客户标签失败: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    @debug
    def get_user_info(self, user_id) -> None:
        """获取指定员工的姓名
        
        Args:
            user_id (str): 员工ID
            
        Returns:
            dict: 包含员工姓名的信息，格式如 {"user_id": "zhangsan", "name": "张三"}
        """
        access_token = self.get_access_token()
        
        url = f"https://qyapi.weixin.qq.com/cgi-bin/user/get"
        params = {
            'access_token': access_token,
            'userid': user_id
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('errcode') == 0:
                # 只返回姓名信息
                return {
                    "user_id": result.get('userid', ''),
                    "name": result.get('name', '')
                }
            else:
                errcode = result.get('errcode', 'unknown')
                errmsg = result.get('errmsg', '未知错误')
                error_msg = f"获取员工信息失败: {errmsg} (错误码: {errcode})"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"请求员工信息失败: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)



def main():
    """测试函数"""
    try:
        # 初始化API客户端
        api = WxWorkAPI()
        
        print("企业微信API测试")
        print("请确保已在.env文件中设置正确的WXWORK_CORP_ID和WXWORK_APP_SECRET")
        print("=" * 50)
        
        # 测试获取access_token
        print("1. 测试获取access_token...")
        token = api.get_access_token()
        print(f"Access Token: {token[:20]}...")
        
        # 测试获取员工信息
        print("2. 测试获取员工信息...")
        
        user_info = api.get_user_info('ChengJianZhang')
        print(f"员工信息: {user_info}")
        
        # 测试创建日程
        print("3. 测试创建日程...")
        
        schedule_info = api.create_schedule('ChengJianZhang', '告知客户白酒到货', '2025-09-15 17:00:00', 30)
        print(f"日程信息: {schedule_info}")

        # 测试发送通知
        print("4. 测试发送通知...")
        notify_info = api.notify_user(
           user_id='ChengJianZhang', 
           msgtype='text',
           title='Sagt 操作确认', 
           content='$userName=ChengJianZhang$，您好，我为您的客户创建了新的企业标签，您可以打开应用查看确认。')
        print(f"通知信息: {notify_info}")
        
        # 测试更新客户标签
        # print("5. 测试更新客户标签...")
        
        # update_info = api.update_customer_tag(
        #     user_id='ChengJianZhang', 
        #     external_id='wmE8gRKQAANU9ioysMc87Qd83d9bcO6g', 
        #     tag_ids_remove=['stE8gRKQAADGVLGdmyeAyART92z5BPdQ', 'stE8gRKQAAnI1bwMxf_tvALaL5tubl8A'], 
        #     tag_ids_add=['etE8gRKQAAs5EIsdA7PTiTsn8GoUSkqg', 'etE8gRKQAAFomsLb5ePOTLlSb_p-Y9mA'])
        # print(f"更新客户标签信息: {update_info}")

    except Exception as e:
        print(f"测试失败: {e}")


if __name__ == "__main__":
    main()
