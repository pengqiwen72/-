"""
云集优选 电商演示数据初始化脚本

用于初始化企业标签、客服坐席与会员信息、会话记录和订单数据的演示脚本。
仅用于本地演示，数据结构与原项目完全一致。
"""

import logging
from datetime import datetime
from ecom_store_api import create_sagt_store_api
import sys
import os
from dotenv import load_dotenv

load_dotenv()


from datetime_string import datetime2timestamp

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

## 测试用的会员ID和坐席ID
DEMO_EXTERNAL_ID = os.getenv("DEMO_EXTERNAL_ID")
DEMO_USER_ID = os.getenv("DEMO_USER_ID")

## 服务账号信息
ECOM_USER_ID = os.getenv("ECOM_USER_ID")
ECOM_USER_PASSWORD = os.getenv("ECOM_USER_PASSWORD")
ECOM_SERVER_URL = os.getenv("ECOM_SERVER_URL")


class DemoDataInitializer:
    """演示数据初始化器"""

    store_client = None

    def __init__(self, server_url: str, user_id: str, password: str):
        self.store_client = create_sagt_store_api(server_url, user_id, password)

    def init_all_data(self):
        """初始化所有演示数据"""
        logger.info("开始初始化演示数据...")

        try:
            # 1. 初始化标签
            self.init_tags() # 注意标签ID和企业微信中的不一致。测试期间可以使用。

            # 2. 初始化坐席和会员信息
            self.init_user_and_customer()

            # 3. 初始化导购会话记录
            self.init_wxqy_messages()

            # 4. 初始化售后会话记录
            self.init_wxkf_messages()

            # 5. 初始化订单记录
            self.init_orders()

            logger.info("演示数据初始化完成！")

        except Exception as e:
            logger.error(f"数据初始化失败: {e}")
            raise

    def init_tags(self):
        """初始化企业标签"""
        logger.info("正在初始化企业标签...")


        # {"tag_id": "tag_004","tag_name": "商务合作","strategy_id": 2,"group_id": "service_type","group_name": "服务类型","deleted": False}

        # 基础属性标签
        basic_tags = [
            {"tag_id": "stE8gRKQAA-dAKMiJZw1MP4s_drOAh2Q", "tag_name": "高客单会员",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAA7TJ7kvuKMeAA7FmcDY8Wg", "tag_name": "一线城市用户",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAdN5SvmZoNc5n6Up12dPp-A", "tag_name": "25-35岁女性用户",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAJvszKCI8cCQJ0paQP2u9Wg", "tag_name": "母婴家庭",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA5s9XrL7mN2b8kGjHtRwQ", "tag_name": "宠物家庭",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA7x2PqW5sT9d6fKlJbNg", "tag_name": "新婚家庭",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA9z4MnB8vC3g1hSdFjKg", "tag_name": "银发用户",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA2w6QeR8tY5u7iOpAsDg", "tag_name": "校园学生党",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA6v3BsN7mK2j9hGfDcXw", "tag_name": "都市白领",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA8y1XdZ6pU4r5tJkLmQe", "tag_name": "自有住房用户",
             "group_id": "stE8gRKQAA4U6yUFhEhAXQ1BHZ5jt0_Q", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
        ]

        # 消费行为标签
        buy_tags = [
            {"tag_id": "stE8gRKQAA3EbL1cO0YS0cyupiATvFqA", "tag_name": "高频复购用户",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAhRYl-8b_Z2hzcimwynFIEA", "tag_name": "大促囤货型",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAASj3DlY9G97EkhUBZmlwgrg", "tag_name": "价格敏感型",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAEJGWP7O1vHTDZn4ep0fWxQ", "tag_name": "新品尝鲜型",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAo-zv5-_oEWt__adANrQK1Q", "tag_name": "品牌忠诚用户",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA1k5JhG8dS2f7gHjKlPoQ", "tag_name": "高端线偏好",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA4m7NpQ9rT1s3dFgHjKwE", "tag_name": "家庭囤货采购",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA7j2LkS5dF8g1hJmNpRt", "tag_name": "直播下单活跃",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA9n6BgV3cX7z2aSdFgHj", "tag_name": "会员复购稳定",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA2p8RdT6fG4h9jKlMnBs", "tag_name": "母婴用品高频",
             "group_id": "stE8gRKQAAdkLtKRp5u1HBHTYaGDbHhA", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
        ]

        # 社交关系标签
        social_tags = [
            {"tag_id": "stE8gRKQAAkgEe-7wvit1OPLqaUV55Hg", "tag_name": "会员社群活跃",
             "group_id": "stE8gRKQAA3IKjjLIzWF7sZ1gy8UJzYQ", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA4yE1-7IkO8SzvIOsCQej9A", "tag_name": "拼团发起人",
             "group_id": "stE8gRKQAA3IKjjLIzWF7sZ1gy8UJzYQ", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA6s7DfG9hJ2k4lMnPqRs", "tag_name": "直播间互动多",
             "group_id": "stE8gRKQAA3IKjjLIzWF7sZ1gy8UJzYQ", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA8d2FgH5jK7l9nOpQrSt", "tag_name": "关注达人种草",
             "group_id": "stE8gRKQAA3IKjjLIzWF7sZ1gy8UJzYQ", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA1f4GhJ6kL8m2nPrQsTu", "tag_name": "爱分享晒单",
             "group_id": "stE8gRKQAA3IKjjLIzWF7sZ1gy8UJzYQ", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
        ]

        # 服务敏感度标签
        service_tags = [
            {"tag_id": "stE8gRKQAAT4k2ZHMxooZjfORj-AdH4A", "tag_name": "要求极速发货",
             "group_id": "stE8gRKQAAYywcJoxIOzqvDLm5XSXi_g", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAAzZCHpp3W4sDwt1vO_EQyVQ", "tag_name": "重视售后响应速度",
             "group_id": "stE8gRKQAAYywcJoxIOzqvDLm5XSXi_g", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA5d7FgH2jK4l6mNpQrSt", "tag_name": "关注正品保障",
             "group_id": "stE8gRKQAAYywcJoxIOzqvDLm5XSXi_g", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA7f9GhJ3kL5m7nOpQrTu", "tag_name": "需要专属客服",
             "group_id": "stE8gRKQAAYywcJoxIOzqvDLm5XSXi_g", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA9h2JkL4mN6p8qRsTuVw", "tag_name": "对运费政策敏感",
             "group_id": "stE8gRKQAAYywcJoxIOzqvDLm5XSXi_g", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "stE8gRKQAA3j5KlM7nP9q2rStUvWx", "tag_name": "关注价保政策",
             "group_id": "stE8gRKQAAYywcJoxIOzqvDLm5XSXi_g", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
        ]


        # 合并所有标签
        all_tags = basic_tags + buy_tags + social_tags + service_tags

        # 批量创建标签
        for tag in all_tags:
            try:
                self.store_client.upsert_tags_setting(
                    tag_id=tag["tag_id"],
                    tag_name=tag["tag_name"],
                    group_id=tag["group_id"],
                    group_name=tag["group_name"],
                    strategy_id=tag["strategy_id"],
                    deleted=tag["deleted"]
                )
                logger.info(f"创建标签: {tag['tag_id']} - {tag['tag_name']}")
            except Exception as e:
                logger.warning(f"标签 {tag['tag_id']} 可能已存在: {e}")

        logger.info(f"标签初始化完成，共处理 {len(all_tags)} 个标签")

    def init_user_and_customer(self):
        """初始化坐席和会员信息"""
        logger.info("正在初始化用户信息...")

        # 1. 创建坐席信息
        employee_data = {
            "user_id": DEMO_USER_ID,
            "name": "张慧",
        }

        try:
            self.store_client.upsert_employee(
                user_id=employee_data["user_id"],
                name=employee_data["name"]
            )
            logger.info(f"创建坐席: {employee_data['user_id']} - {employee_data['name']}")
        except Exception as e:
            logger.warning(f"坐席 {employee_data['user_id']} 可能已存在: {e}")

        # 2. 创建会员信息
        customer_data = {
            "external_id": DEMO_EXTERNAL_ID,
            "name": "dacheng",
            "remark_name": "程哥",
            "union_id": "union001",
            "follow_user_id": DEMO_USER_ID,
            "tags": ["etE8gRKQAANeNZh8ttsqCe-S-XIEsPdQ", "etE8gRKQAA6xbQIT5mE62_WSKeiWVbAg", "etE8gRKQAAdm3IQ7SVSEtb5BrRzQJHnQ"]
        }

        try:
            self.store_client.upsert_external_user(
                external_id=customer_data["external_id"],
                union_id=customer_data["union_id"],
                follow_user_id=customer_data["follow_user_id"],
                name = customer_data["name"],
                remark_name = customer_data["remark_name"],
                tags = customer_data["tags"],
            )
            logger.info(f"创建会员: {customer_data['external_id']} - {customer_data['name']}")
        except Exception as e:
            logger.warning(f"会员 {customer_data['external_id']} 可能已存在: {e}")

        logger.info("坐席和会员信息初始化完成")

    def init_wxqy_messages(self):
        """初始化导购会话记录（企业微信）"""
        logger.info("正在初始化导购会话记录...")

        # {"msg_id": "wxqy_002", "from_id": "ext_001", "to_id": "emp_001", "content": "请问贵公司的技术方案有哪些优势？", "msg_time": 3000, "seq": 2}

        # 导购会话记录
        wxqy_messages = [
            # 冬季新品推荐
            {"seq": 1, "msg_id": "wxqy_001", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-11-01 09:15:00"),
             "content": "程哥，您上次看的那款轻薄羽绒服这周会员日直降 200，还送同色围巾，需要我先帮您锁一件吗？"},
            {"seq": 2, "msg_id": "wxqy_002", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-11-06 09:20:00"),
             "content": "正好要买冬装，帮我留一件黑色的 L 码。"},
            {"seq": 3, "msg_id": "wxqy_003", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-11-12 09:22:00"),
             "content": "已经帮您锁定了，黑色 L 码现货，今晚 8 点前下单还能叠加满 599 减 80 的券。"},

            # 大促囤货
            {"seq": 4, "msg_id": "wxqy_004", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-11-17 11:05:00"),
             "content": "程哥，双 11 预售今晚开启，您常买的那款每日坚果礼盒，前 1000 名付定金送同款小包装。"},
            {"seq": 5, "msg_id": "wxqy_005", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-11-23 11:15:00"),
             "content": "那正好，我把家里要囤的清单一起发你，顺便凑个满减。"},
            {"seq": 6, "msg_id": "wxqy_006", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-11-28 11:20:00"),
             "content": "好的，我算了一下：坚果两箱 + 纯牛奶一箱刚好满 398 减 60，差 12 元就能再用 5 元无门槛券，帮您配齐。"},

            # 换码咨询
            {"seq": 7, "msg_id": "wxqy_007", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-12-04 10:20:00"),
             "content": "上次买的那双跑鞋有点磨脚，能换大半码吗？"},
            {"seq": 8, "msg_id": "wxqy_008", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
            "msg_time": datetime2timestamp("2025-12-09 10:25:00"),
            "content": "可以的，7 天内不影响二次销售就能换码，我给您发起换货单，快递会上门取件。"},

            # 礼盒采购
            {"seq": 9, "msg_id": "wxqy_009", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-12-15 14:05:00"),
             "content": "那款儿童保温杯有货吗？想买来送人的。"},
            {"seq": 10, "msg_id": "wxqy_010", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-12-20 14:10:00"),
             "content": "有的，礼盒装带贺卡，下单备注留言我们免费包装，会员日再打 9 折，送人很合适。"},

            # 物流异常
            {"seq": 11, "msg_id": "wxqy_011", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-12-25 10:12:00"),
             "content": "我买的空气炸锅显示已签收，但家里没收到。"},
            {"seq": 12, "msg_id": "wxqy_012", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-12-31 10:20:00"),
             "content": "我这边查到物流放在小区驿站了，已经联系快递核实，2 小时内给您回复，实在找不到我们直接补发。"},

            # 会员等级升级
            {"seq": 13, "msg_id": "wxqy_013", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-01-05 09:30:00"),
             "content": "程哥，您的会员等级升到黑卡了，专属客服通道和每月免运费券已经到账，可以在我的-会员中心查看。"},
            {"seq": 14, "msg_id": "wxqy_014", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-01-11 09:33:00"),
             "content": "这个不错，以后买大件就直接找你了。"},

            # 家纺复购
            {"seq": 15, "msg_id": "wxqy_015", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-01-16 14:22:00"),
             "content": "618 那套真丝四件套还有货吗？想再买一套送爸妈。"},
            {"seq": 16, "msg_id": "wxqy_016", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-01-22 14:25:00"),
             "content": "同款刚补货，还有 40 套，会员第二件 8 折，我先帮您留一套雾霾蓝的。"},

            # 配送时段
            {"seq": 17, "msg_id": "wxqy_017", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-01-27 16:45:00"),
             "content": "你们生鲜配送能不能改到晚上？白天家里没人收货。"},
            {"seq": 18, "msg_id": "wxqy_018", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-02-01 16:50:00"),
             "content": "可以选 18:00-21:00 时段，下单时在配送时间栏里选一下就行，我这边也帮您备注了晚上送。"},

            # 正品咨询
            {"seq": 19, "msg_id": "wxqy_019", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-02-07 15:10:00"),
             "content": "上次买的洗发水是小样吗？感觉比专柜小一点。"},
            {"seq": 20, "msg_id": "wxqy_020", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-02-12 15:15:00"),
             "content": "是正装 500ml，和专柜同规格，我把批次号和防伪查询链接发您，可以扫码核验。"},

            # 开抢提醒
            {"seq": 21, "msg_id": "wxqy_021", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-02-18 17:30:00"),
             "content": "程哥，您收藏的那款扫地机今晚 0 点开抢，前 200 名送滤芯套装，需要提醒您吗？"},
            {"seq": 22, "msg_id": "wxqy_022", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-02-23 17:35:00"),
             "content": "要的，到时候提醒我一下，我怕忘了。"},

            # 售后说明
            {"seq": 23, "msg_id": "wxqy_023", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-03-01 10:15:00"),
             "content": "已经帮您设置开抢提醒，到点会在微信服务通知里推给您。另外退货运费这边说明一下：质量问题我们承担，七天无理由需自理，有运费险可以直接抵扣。"},
            {"seq": 24, "msg_id": "wxqy_024", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-03-06 10:20:00"),
             "content": "明白了，服务挺细致的，那我等通知。"},

            # 秋装上新
            {"seq": 25, "msg_id": "wxqy_025", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2026-03-11 10:30:00"),
             "content": "程哥，秋装上新了，您常买的那个牌子出了新款风衣，会员可以先试穿后付款，我给您留一件？"},
            {"seq": 26, "msg_id": "wxqy_026", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2026-03-17 10:35:00"),
             "content": "留一件卡其色 M 码，我周末去门店试试。"}
        ]

        # 批量创建企业微信消息
        for msg in wxqy_messages:
            try:
                self.store_client.upsert_wxqy_msg(
                    msg_id=msg["msg_id"],
                    from_id=msg["from_id"],
                    to_id=msg["to_id"],
                    content=msg["content"],
                    msg_time=msg["msg_time"],
                    seq=msg["seq"]
                )
                logger.info(f"创建导购会话: {msg['msg_id']}")
            except Exception as e:
                logger.warning(f"导购会话 {msg['msg_id']} 可能已存在: {e}")

        logger.info(f"导购会话记录初始化完成，共处理 {len(wxqy_messages)} 条消息")

    def init_wxkf_messages(self):
        """初始化售后客服会话记录"""
        logger.info("正在初始化售后客服会话记录...")

        # {"msg_id": "msg_kf_001", "external_id": "dacheng001", "open_kfid": "kf001", "servicer_userid": "xiaozhang", "msg_time": 600, "origin": 5, msgtype": "text", "content": "关于定制化方案的详细信息，我已经发送到您的邮箱"}
        # 售后客服会话记录
        wxkf_messages = [
            # 尺码与库存咨询
            {"msg_id": "msg_kf_001", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-03-22 09:05:00"), "origin": 3,
             "msgtype": "text", "content": "这款羽绒服有 175 和 180 两个码吗？我 178 高，选哪个合适？"},
            {"msg_id": "msg_kf_002", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-03-28 09:10:00"), "origin": 5,
             "msgtype": "text",
             "content": "您好！这款版型偏合身，178 建议选 180 码，里面还能加一件毛衣。两个码都有现货，会员日下单再减 200 元~"},

            # 运费与配送咨询
            {"msg_id": "msg_kf_003", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-04-02 14:20:00"), "origin": 3,
             "msgtype": "text", "content": "下单多久发货？偏远地区包邮吗？"},
            {"msg_id": "msg_kf_004", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-04-08 14:25:00"), "origin": 5,
             "msgtype": "text",
             "content": "现货商品 48 小时内发出，江浙沪皖次日达；单笔满 99 元全国包邮（新疆西藏满 199 包邮），黑卡会员每月还有 3 张免运费券。"},

            # 库存提醒
            {"msg_id": "msg_kf_005", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-04-13 11:00:00"), "origin": 5,
             "msgtype": "text",
             "content": "您好，您关注的真丝四件套已经补货，雾霾蓝 1.8m 还有 12 套，需要我帮您留一套吗？"},
            {"msg_id": "msg_kf_006", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-04-19 15:15:00"), "origin": 3,
             "msgtype": "text", "content": "留一套，另外这款支持货到付款吗？"},
            {"msg_id": "msg_kf_007", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-04-24 15:20:00"), "origin": 5,
             "msgtype": "text",
             "content": "您好，家纺类目支持货到付款，验货后签收即可，不过线上支付可以叠加会员日满减，会更划算一些。"},

            # 退换货
            {"msg_id": "msg_kf_008", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-04-29 10:05:00"), "origin": 3,
             "msgtype": "text", "content": "收到的小家电外壳有划痕，能换一台新的吗？"},
            {"msg_id": "msg_kf_009", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-05-05 10:10:00"), "origin": 5,
             "msgtype": "text",
             "content": "非常抱歉！外壳划痕属于运输问题，我们直接给您换新：今天安排上门取件，新机 48 小时内发出，运费我们承担。"},

            # 续费/会员权益
            {"msg_id": "msg_kf_010", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-05-10 16:30:00"), "origin": 5,
             "msgtype": "text",
             "content": "温馨提示：您的黑卡会员本月 30 日到期，续费可享年度最低价，还能额外获得 2 张无门槛券和生日礼包。"},

            # 价格与价保
            {"msg_id": "msg_kf_011", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-05-16 13:40:00"), "origin": 3,
             "msgtype": "text", "content": "这个扫地机现在买 1999？下周大促会不会更便宜？"},
            {"msg_id": "msg_kf_012", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-05-21 13:45:00"), "origin": 5,
             "msgtype": "text",
             "content": "现在是 1999 元的会员价，大促最低到 1899。我们支持 15 天价保，现在买如果降价，差价可以申请退回，不用担心买贵。"},

            # 保质期/生产日期
            {"msg_id": "msg_kf_013", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-05-27 18:10:00"), "origin": 3,
             "msgtype": "text", "content": "坚果礼盒生产日期是什么时候的？保质期多久？"},
            {"msg_id": "msg_kf_014", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-06-01 18:15:00"), "origin": 5,
             "msgtype": "text",
             "content": "礼盒为 2025 年 3 月生产批次，保质期 9 个月，独立小包装锁鲜，开封后建议两周内食用完。"},

            # 补发
            {"msg_id": "msg_kf_015", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-06-07 09:20:00"), "origin": 5,
             "msgtype": "text", "content": "您好，您反馈漏发的那件赠品已经安排补发，物流单号稍后发您，本次补发不收运费。"},
            {"msg_id": "msg_kf_016", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-06-12 14:50:00"), "origin": 3,
             "msgtype": "text", "content": "好的，那原来那单不用退了吧？"},
            {"msg_id": "msg_kf_017", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-06-17 14:55:00"), "origin": 5,
             "msgtype": "text",
             "content": "不用退回，已收到的商品正常使用即可，补发部分预计 2 天后送达。"},

            # 发票
            {"msg_id": "msg_kf_018", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-06-23 10:30:00"), "origin": 3,
             "msgtype": "text", "content": "订单可以开公司抬头的发票吗？"},
            {"msg_id": "msg_kf_019", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-06-28 10:35:00"), "origin": 5,
             "msgtype": "text",
             "content": "可以的，支持开具电子普通发票，个人和公司抬头均可，在订单详情里提交开票信息，1-3 个工作日发送到邮箱。"},

            # 优惠券
            {"msg_id": "msg_kf_020", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-07-04 08:00:00"), "origin": 5,
             "msgtype": "text", "content": "您好，您账户里有 3 张即将过期的优惠券，本周内下单可以叠加使用，需要我帮您看看能买什么吗？"},
            {"msg_id": "msg_kf_021", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-07-09 08:05:00"), "origin": 3,
             "msgtype": "text", "content": "能叠加吗？还是有使用门槛？"},
            {"msg_id": "msg_kf_022", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-07-15 08:10:00"), "origin": 5,
             "msgtype": "text", "content": "满 199 减 20 的券可以和会员折扣叠加，但不能和百亿补贴商品同享，我帮您把能用的商品组合列一下。"},

            # 正品与授权
            {"msg_id": "msg_kf_023", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-07-20 19:15:00"), "origin": 3,
             "msgtype": "text", "content": "你们店是品牌授权的吗？担心买到假货。"},
            {"msg_id": "msg_kf_024", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-07-25 19:20:00"), "origin": 5,
             "msgtype": "text",
             "content": "我们是品牌官方授权店铺，店铺首页可以查看授权书，每件商品带防伪码，支持专柜验货，假一赔十。"},

            # 老带新活动
            {"msg_id": "msg_kf_025", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-07-31 11:30:00"), "origin": 5,
             "msgtype": "text", "content": "老会员推荐新用户下单，双方各得 30 元无门槛券，您有需要可以分享专属邀请码。"},
            {"msg_id": "msg_kf_026", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-08-05 15:25:00"), "origin": 3,
             "msgtype": "text", "content": "线上买的和线下门店是同款吗？价格一样吗？"},
            {"msg_id": "msg_kf_027", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-08-11 15:30:00"), "origin": 5,
             "msgtype": "text",
             "content": "同款同规格，线上有会员专享价通常更优惠，线下门店可以试穿试戴，支持门店自提和线上下单门店退换。"},

            # 大促玩法
            {"msg_id": "msg_kf_028", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-08-16 12:40:00"), "origin": 3,
             "msgtype": "text", "content": "618 的满减和店铺券能一起用吗？怎么买最便宜？"},
            {"msg_id": "msg_kf_029", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-08-22 12:45:00"), "origin": 5,
             "msgtype": "text",
             "content": "可以叠加：平台满 300 减 50 + 店铺满 199 减 20 + 会员 95 折，先付定金再付尾款最划算，我给您整理一份下单顺序。"},

            # 以旧换新
            {"msg_id": "msg_kf_030", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2026-08-27 11:00:00"), "origin": 3,
             "msgtype": "text", "content": "旧的小家电能以旧换新吗？怎么操作？"}
        ]

        # 批量创建微信客服消息
        for msg in wxkf_messages:
            try:
                self.store_client.upsert_wxkf_msg(
                    msg_id=msg["msg_id"],
                    external_id=msg.get("external_id"),
                    open_kfid=msg.get("open_kfid"),
                    servicer_userid=msg.get("servicer_userid"),
                    msg_time=msg.get("msg_time"),
                    origin=msg.get("origin"),
                    msgtype=msg.get("msgtype"),
                    content=msg.get("content")
                )
                logger.info(f"创建售后会话: {msg['msg_id']}")
            except Exception as e:
                logger.warning(f"售后会话 {msg['msg_id']} 可能已存在: {e}")

        logger.info(f"售后会话记录初始化完成，共处理 {len(wxkf_messages)} 条消息")

    def init_orders(self):
        """初始化订单记录"""
        logger.info("正在初始化订单记录...")


        # {"union_id": "union_001","order_id": "order_001","open_id": "open_001","order_status": 100,"order_products": ["企业级解决方案"],"order_price": 2999.99,"order_create_time": base_create_time, "order_raw_info": {"memo": ""}}
        # 微信小店订单记录
        orders = [
            {"union_id": "union001", "order_id": "WX20240908", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-09-01 10:00:00"), "order_price": 899.00,
             "order_products": ["云集优选·轻薄羽绒服（黑色 L）"],
             "order_raw_info": {"memo": "会员日立减 200，冬季换季添置"}},
            {"union_id": "union001", "order_id": "WX20240928", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-09-07 10:00:00"), "order_price": 328.00,
             "order_products": ["每日坚果混合装礼盒（30 袋）"], "order_raw_info": {"memo": "大促囤货，凑单满减"}},
            {"union_id": "union001", "order_id": "WX20241015", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-09-12 10:00:00"), "order_price": 1280.00,
             "order_products": ["云集优选·真丝四件套 1.8m（雾霾蓝）"],
             "order_raw_info": {"memo": "家纺升级，替换旧床品"}},
            {"union_id": "union001", "order_id": "WX20241120", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-09-18 10:00:00"), "order_price": 158.00,
             "order_products": ["儿童保温杯礼盒装（含贺卡）"], "order_raw_info": {"memo": "送朋友小孩的生日礼物"}},
            {"union_id": "union001", "order_id": "WX20250110", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-09-23 10:00:00"), "order_price": 469.00,
             "order_products": ["家用空气炸锅 5L 可视款"], "order_raw_info": {"memo": "厨房小家电，春节前入手"}},
            {"union_id": "union001", "order_id": "WX20250218", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-09-29 10:00:00"), "order_price": 599.00,
             "order_products": ["男士缓震跑鞋（43 码）"], "order_raw_info": {"memo": "换码一次，最终 43 码"}},
            {"union_id": "union001", "order_id": "WX20250425", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2026-10-04 10:00:00"), "order_price": 258.00,
             "order_products": ["氨基酸洗发水 500ml 双支装"], "order_raw_info": {"memo": "家清日化补货"}},
            {"union_id": "union001", "order_id": "WX20250622", "open_id": "open001", "order_status": 30,
             "order_create_time": datetime2timestamp("2026-10-10 10:00:00"), "order_price": 1899.00,
             "order_products": ["扫地机器人 自清洁版"], "order_raw_info": {"memo": "618 前 200 名送滤芯套装"}},
            {"union_id": "union001", "order_id": "WX20250810", "open_id": "open001", "order_status": 20,
             "order_create_time": datetime2timestamp("2026-10-15 10:00:00"), "order_price": 138.00,
             "order_products": ["有机纯牛奶 250ml*24 礼盒"], "order_raw_info": {"memo": "生鲜乳品定期补货"}},
        ]

        # 批量创建订单
        for order in orders:
            try:
                self.store_client.upsert_wxxd_order(
                    union_id=order["union_id"],
                    order_id=order["order_id"],
                    open_id=order["open_id"],
                    order_status=order["order_status"],
                    order_products=order["order_products"],
                    order_price=order["order_price"],
                    order_create_time=order["order_create_time"],
                    order_raw_info=order["order_raw_info"],
                )
                logger.info(f"创建订单: {order['order_id']} - {order['order_products']}")
            except Exception as e:
                logger.warning(f"订单 {order['order_id']} 可能已存在: {e}")

        logger.info(f"订单记录初始化完成，共处理 {len(orders)} 个订单")


# 主函数和命令行入口
def main():
    """主函数"""
    initializer = DemoDataInitializer(ECOM_SERVER_URL, ECOM_USER_ID, ECOM_USER_PASSWORD)
    #initializer.init_tags()
    #initializer.init_user_and_customer()
    #initializer.init_wxqy_messages()
    #initializer.init_wxkf_messages()
    #initializer.init_orders()
    initializer.init_all_data()


if __name__ == "__main__":
    # 运行初始化脚本
    main()
