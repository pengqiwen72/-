"""
ScmStoreAPI 真实环境数据初始化脚本


注意：
- 本脚本会创建真实数据，请在测试环境中运行
- 支持清理模式，可以删除初始化的数据
- 数据使用特殊前缀以便识别和管理
"""

from typing import Optional, List
from pprint import pprint
from dotenv import load_dotenv
import os

from scm_store_api import create_scm_store_api


load_dotenv()

SCM_SERVER_URL = os.getenv("SCM_SERVER_URL")
SCM_USER_ID = os.getenv("SCM_USER_ID")
SCM_USER_PASSWORD = os.getenv("SCM_USER_PASSWORD")




class DataInitializer:
    """数据初始化器"""
    
    store_client = None

    def __init__(self, server_url: str, user_id: str, password: str):
        self.store_client = create_scm_store_api(server_url, user_id, password)
     
        

    def test_cleanup(self, prefix: Optional[List[str]] = None):
        """测试清理"""

        namespaces = self.store_client.list_all_namespace(prefix=prefix).get("namespaces", [])
        pprint(namespaces)

        for namespace in namespaces:
            pprint(50 * "-")
            pprint(namespace)
            objs = self.store_client.search_items(namespace, limit = 200).get("items", [])
            for obj in objs:
                pprint(50 * "=")
                pprint(obj)
                self.store_client.delete_item(namespace, obj.get("key"))
        

    def show_all(self, prefix: Optional[List[str]] = None):
        """测试清理"""

        namespaces = self.store_client.list_all_namespace(prefix=prefix).get("namespaces", [])
        pprint(namespaces)

        for namespace in namespaces:
            pprint(50 * "-")
            pprint(namespace)
            objs = self.store_client.search_items(namespace = namespace, limit = 100).get("items", [])
            pprint("数量：" + str(len(objs)))
            for obj in objs:
                pprint(50 * "=")
                pprint(obj)

    def show_namespace(self, prefix: Optional[list[str]] = None, suffix: Optional[list[str]] = None):
        namespaces = self.store_client.list_all_namespace(prefix=prefix, suffix=suffix).get("namespaces", [])
        for namespace in namespaces:
            pprint(namespace)

    


def show_all(prefix):
    initializer = DataInitializer(SCM_SERVER_URL, SCM_USER_ID, SCM_USER_PASSWORD)
    #initializer.show_all(prefix=["external_user"])    
    initializer.show_all(prefix=prefix)

def test_cleanup(prefix):
    initializer = DataInitializer(SCM_SERVER_URL, SCM_USER_ID, SCM_USER_PASSWORD)
    initializer.test_cleanup(prefix=prefix)

def show_namespace(prefix):
    initializer = DataInitializer(SCM_SERVER_URL, SCM_USER_ID, SCM_USER_PASSWORD)
    initializer.show_namespace(prefix=prefix)
    #initializer.show_namespace(prefix=["external_user","*"])


if __name__ == "__main__":
    #test_cleanup(prefix=["tags_setting"])
    #show_namespace(prefix=["external_user"])
    show_all(prefix=["*"])