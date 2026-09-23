"""
Cs 演示数据初始化脚本

用于初始化企业标签、员工客户信息、聊天记录和订单数据的演示脚本。
"""

import logging
from datetime import datetime
from cs_store_api import create_cs_store_api
import sys
import os
from dotenv import load_dotenv

load_dotenv()


from datetime_string import datetime2timestamp

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

## 测试用的客户ID和员工ID
DEMO_EXTERNAL_ID = os.getenv("DEMO_EXTERNAL_ID")
DEMO_USER_ID = os.getenv("DEMO_USER_ID")

## Cs账号信息
CS_USER_ID = os.getenv("CS_USER_ID")
CS_USER_PASSWORD = os.getenv("CS_USER_PASSWORD")
CS_SERVER_URL = os.getenv("CS_SERVER_URL")


class DemoDataInitializer:
    """演示数据初始化器"""
    
    store_client = None

    def __init__(self, server_url: str, user_id: str, password: str):
        self.store_client = create_cs_store_api(server_url, user_id, password)
        
    def init_all_data(self):
        """初始化所有演示数据"""
        logger.info("开始初始化演示数据...")
        
        try:
            # 1. 初始化标签
            self.init_tags() # 注意标签ID和企业微信中的不一致。测试期间可以使用。
            
            # 2. 初始化用户信息
            self.init_user_and_customer()
            
            # 3. 初始化聊天记录
            self.init_wxqy_messages()

            # 4. 初始化聊天记录
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
            {"tag_id": "st_demo_tag_0003", "tag_name": "高教育投入家庭",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0026", "tag_name": "学龄段家长",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0032", "tag_name": "独生子女家庭",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0028", "tag_name": "二胎多孩家庭",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0015", "tag_name": "小学阶段学生家长",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0020", "tag_name": "初中阶段学生家长",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0025", "tag_name": "高中阶段学生家长",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0007", "tag_name": "全职陪读家长",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0017", "tag_name": "双职工忙碌家长",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0022", "tag_name": "重点学区住户",
             "group_id": "st_demo_tag_0011", "group_name": "基础属性", "strategy_id": 2,
             "deleted": False},
        ]
        
        # 消费行为标签
        buy_tags = [
            {"tag_id": "st_demo_tag_0008", "tag_name": "学科培优课程偏好",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0034", "tag_name": "升学冲刺班购买",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0029", "tag_name": "高频教辅资料采购",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0027", "tag_name": "寒暑假集训营采购",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0036", "tag_name": "素质拓展课程需求",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0005", "tag_name": "一对一私教付费",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0012", "tag_name": "小班课批量购课",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0019", "tag_name": "升学真题题库购买",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0024", "tag_name": "线上录播课续费用户",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0006", "tag_name": "竞赛培优专项消费",
             "group_id": "st_demo_tag_0033", "group_name": "消费行为", "strategy_id": 2,
             "deleted": False},
        ]
        
        # 社交关系标签
        social_tags = [
            {"tag_id": "st_demo_tag_0035", "tag_name": "家长社群活跃",
             "group_id": "st_demo_tag_0009", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0013", "tag_name": "邻里育儿交流多",
             "group_id": "st_demo_tag_0009", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0016", "tag_name": "家校沟通频繁",
             "group_id": "st_demo_tag_0009", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0021", "tag_name": "学霸家长圈层社交",
             "group_id": "st_demo_tag_0009", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0004", "tag_name": "小众竞赛家长交流",
             "group_id": "st_demo_tag_0009", "group_name": "社交关系", "strategy_id": 2,
             "deleted": False},
        ]
        
        # 服务敏感度标签
        service_tags = [
            {"tag_id": "st_demo_tag_0030", "tag_name": "需优先预留学位名额",
             "group_id": "st_demo_tag_0031", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0037", "tag_name": "依赖专业学情分析服务",
             "group_id": "st_demo_tag_0031", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0014", "tag_name": "重视课后答疑服务",
             "group_id": "st_demo_tag_0031", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0018", "tag_name": "关注学情实时反馈",
             "group_id": "st_demo_tag_0031", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0023", "tag_name": "需要定制化学习方案",
             "group_id": "st_demo_tag_0031", "group_name": "服务敏感度", "strategy_id": 2,
             "deleted": False},
            {"tag_id": "st_demo_tag_0010", "tag_name": "敏感师资变动信息",
             "group_id": "st_demo_tag_0031", "group_name": "服务敏感度", "strategy_id": 2,
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
        """初始化员工和客户信息"""
        logger.info("正在初始化用户信息...")
        
        # 1. 创建员工信息
        employee_data = {
            "user_id": DEMO_USER_ID,
            "name": "小张",
        }
        
        try:
            self.store_client.upsert_employee(
                user_id=employee_data["user_id"],
                name=employee_data["name"]
            )
            logger.info(f"创建员工: {employee_data['user_id']} - {employee_data['name']}")
        except Exception as e:
            logger.warning(f"员工 {employee_data['user_id']} 可能已存在: {e}")
        
        # 2. 创建客户信息
        customer_data = {
            "external_id": DEMO_EXTERNAL_ID,
            "name": "dacheng",
            "remark_name": "程哥",
            "union_id": "union001",
            "follow_user_id": DEMO_USER_ID,
            "tags": ["et_demo_tag_0004", "et_demo_tag_0003", "et_demo_tag_0005"]
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
            logger.info(f"创建客户: {customer_data['external_id']} - {customer_data['name']}")
        except Exception as e:
            logger.warning(f"客户 {customer_data['external_id']} 可能已存在: {e}")
        
        logger.info("员工和客户信息初始化完成")
    
    def init_wxqy_messages(self):
        """初始化企业微信聊天记录"""
        logger.info("正在初始化企业微信聊天记录...")
        
        # {"msg_id": "wxqy_002", "from_id": "ext_001", "to_id": "emp_001", "content": "请问贵公司的技术方案有哪些优势？", "msg_time": 3000, "seq": 2}

        # 企业微信聊天记录
        wxqy_messages = [
            # 小升初冲刺课程咨询
            {"seq": 1, "msg_id": "wxqy_001", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2024-09-01 09:15:00"),
             "content": "李家长，孩子六年级小升初关键期，我们新开的冲刺培优班针对性很强，需要给您预留名额吗！"},
            {"seq": 2, "msg_id": "wxqy_002", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2024-09-01 09:20:00"),
             "content": "太及时了！孩子数学薄弱，正好需要冲刺课程，帮我留一个周末班名额。"},
            {"seq": 3, "msg_id": "wxqy_003", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2024-09-01 09:22:00"),
             "content": "没问题！我帮您登记优先名额，附赠专属学情测评，稍后发您预约链接。"},

            # 国庆集训营报名咨询
            {"seq": 4, "msg_id": "wxqy_004", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2024-09-25 11:05:00"),
             "content": "李家长，国庆7天全科集训营开启报名，专攻薄弱知识点，现在报名有早鸟优惠！"},
            {"seq": 5, "msg_id": "wxqy_005", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2024-09-25 11:15:00"),
             "content": "刚好假期没人辅导孩子，报全科的，有没有小班授课？"},
            {"seq": 6, "msg_id": "wxqy_006", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2024-09-25 11:20:00"),
             "content": "有的！我们是10人精品小班，师资都是重点校骨干教师，集训后附赠阶段性学情报告。"},

            # 一对一私教定制咨询
            {"seq": 7, "msg_id": "wxqy_007", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2024 - 10 - 12 10: 20:00"),
             "content": "老师，孩子英语偏科严重，大班课跟不上，能不能安排一对一私教？"},

            {"seq": 8, "msg_id": "wxqy_008", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
            "msg_time": datetime2timestamp("2024-10-12 10:25:00"),
            "content": "可以的！我们可以匹配英语专项资深老师，先做学情诊断，定制专属提升方案，针对性补短板。"},

            # 教辅资料采购咨询
            {"seq": 9, "msg_id": "wxqy_009", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2024-11-05 14:05:00"),
             "content": "请问有没有六年级小升初真题汇编资料？最新版本的。"},
            {"seq": 10, "msg_id": "wxqy_010", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2024-11-05 14:10:00"),
             "content": "有的！最新2024年各地小升初真题合集，含详细解析和答题技巧，学员购买有专属折扣，我发您链接。"},

            # 学情报告解读咨询
            {"seq": 11, "msg_id": "wxqy_011", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2024-12-08 10:12:00"),
             "content": "孩子这次月考学情报告我看不太懂，能帮忙解读一下薄弱点吗？"},
            {"seq": 12, "msg_id": "wxqy_012", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2024-12-08 10:20:00"),
             "content": "没问题！我联系授课老师一对一为您解读，梳理知识漏洞，给出后续针对性学习规划。"},

            # 寒假集训营名额预留
            {"seq": 13, "msg_id": "wxqy_013", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-01-05 09:30:00"),
             "content": "李家长，寒假中考冲刺集训营名额紧张，优先给老学员预留，需要帮您锁定吗？"},
            {"seq": 14, "msg_id": "wxqy_014", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-01-05 09:33:00"),
             "content": "锁定！马上寒假了，必须抓紧冲刺，千万别没名额了。"},

            # 数理化竞赛课程咨询
            {"seq": 15, "msg_id": "wxqy_015", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-02-10 14:22:00"),
             "content": "孩子想参加数学竞赛，你们有没有专项培优课程？"},
            {"seq": 16, "msg_id": "wxqy_016", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-02-10 14:25:00"),
             "content": "有的！我们有专属数理化竞赛培优班，由竞赛获奖导师授课，包含刷题、模考、赛前冲刺全套服务。"},

            # 师资更换咨询确认
            {"seq": 17, "msg_id": "wxqy_017", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-03-18 16:45:00"),
             "content": "听说后续授课老师会调整，新老师教学经验怎么样？"},
            {"seq": 18, "msg_id": "wxqy_018", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-03-18 16:50:00"),
             "content": "新老师都是5年以上毕业班教学经验，擅长小升初冲刺，我把老师简历和教学成果发您参考。"},

            # 素质课程报名咨询
            {"seq": 19, "msg_id": "wxqy_019", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-04-20 15:10:00"),
             "content": "除了学科课，有没有语文阅读写作的素质提升课程？"},
            {"seq": 20, "msg_id": "wxqy_020", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-04-20 15:15:00"),
             "content": "有的！阅读写作专项课主打提升语感和答题技巧，适合小学高年级学员，小班教学效果很好。"},

            # 期末冲刺辅导推荐
            {"seq": 21, "msg_id": "wxqy_021", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-06-20 17:30:00"),
             "content": "李家长，期末临近，专属冲刺刷题班开课了，帮孩子巩固知识点！"},
            {"seq": 22, "msg_id": "wxqy_022", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-06-20 17:35:00"),
             "content": "太及时了！马上报名，孩子期末复习没有方向，正好需要系统辅导。"},

            # 学员升学成果反馈
            {"seq": 23, "msg_id": "wxqy_023", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-07-15 10:15:00"),
             "content": "恭喜您孩子小升初顺利考入重点初中！这一年的培优冲刺效果非常显著！"},
            {"seq": 24, "msg_id": "wxqy_024", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-07-15 10:20:00"),
             "content": "多亏了你们的课程和老师耐心辅导，后续初中课程我还优先选你们家！"},

            # 初中衔接班咨询
            {"seq": 25, "msg_id": "wxqy_025", "from_id": DEMO_USER_ID, "to_id": DEMO_EXTERNAL_ID,
             "msg_time": datetime2timestamp("2025-08-05 10:30:00"),
             "content": "李家长，孩子刚升初中，我们新初一衔接班正在招生，帮孩子平稳过渡学段！"},
            {"seq": 26, "msg_id": "wxqy_026", "from_id": DEMO_EXTERNAL_ID, "to_id": DEMO_USER_ID,
             "msg_time": datetime2timestamp("2025-08-05 10:35:00"),
             "content": "正想咨询这个！初中知识点难度提升大，必须提前衔接，给我报名精品小班。"}
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
                logger.info(f"创建企业微信消息: {msg['msg_id']}")
            except Exception as e:
                logger.warning(f"企业微信消息 {msg['msg_id']} 可能已存在: {e}")
        
        logger.info(f"企业微信聊天记录初始化完成，共处理 {len(wxqy_messages)} 条消息")

    def init_wxkf_messages(self):
        """初始化微信客服聊天记录"""
        logger.info("正在初始化微信客服聊天记录...")
        
        # {"msg_id": "msg_kf_001", "external_id": "dacheng001", "open_kfid": "kf001", "servicer_userid": "xiaozhang", "msg_time": 600, "origin": 5, msgtype": "text", "content": "关于定制化方案的详细信息，我已经发送到您的邮箱"}
        # 微信客服聊天记录
        wxkf_messages = [
            # 课程班次与价格咨询
            {"msg_id": "msg_kf_001", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-02 09:05:00"), "origin": 3,
             "msgtype": "text", "content": "请问六年级小升初冲刺班有周末班和周中班吗？价格分别是多少？"},
            {"msg_id": "msg_kf_002", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-02 09:10:00"), "origin": 5,
             "msgtype": "text",
             "content": "家长您好！目前开设周六上午、周日下午周末班，周中晚班仅针对老生，新学员优先报周末班，本期早鸟价立减800元~"},

            # 学情测评服务咨询
            {"msg_id": "msg_kf_003", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-03 14:20:00"), "origin": 3,
             "msgtype": "text", "content": "新学员可以免费做全科学情测评吗？想精准知道孩子薄弱科目。"},
            {"msg_id": "msg_kf_004", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-03 14:25:00"), "origin": 5,
             "msgtype": "text",
             "content": "可以的！所有新学员均可享受免费全科学情测评，测评后生成专属分析报告，老师一对一解读，定制学习方案。"},

            # 集训营师资咨询
            {"msg_id": "msg_kf_005", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-26 11:00:00"), "origin": 5,
             "msgtype": "text",
             "content": "家长您好，您预约的国庆集训营师资已确定，均为重点校在职骨干教师，附带多年毕业班教学经验。"},
            {"msg_id": "msg_kf_006", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-26 15:15:00"), "origin": 3,
             "msgtype": "text", "content": "小班课和一对一课程的老师是同一批吗？师资水平有区别吗？"},
            {"msg_id": "msg_kf_007", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-09-26 15:20:00"), "origin": 5,
             "msgtype": "text",
             "content": "核心师资团队统一授课，一对一课程会匹配专项短板老师，针对性更强，小班课侧重系统培优，师资质量无差异。"},

            # 课程售后与补课咨询
            {"msg_id": "msg_kf_008", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-10-05 10:05:00"), "origin": 3,
             "msgtype": "text", "content": "孩子生病缺了一节课，能不能安排免费补课？"},
            {"msg_id": "msg_kf_009", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-10-05 10:10:00"), "origin": 5,
             "msgtype": "text",
             "content": "可以的！学员缺课均可免费预约一对一补课，同时补发本节课课件和习题资料，保障学习进度不落下。"},

            # 续费优惠活动咨询
            {"msg_id": "msg_kf_010", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2024-12-01 16:30:00"), "origin": 5,
             "msgtype": "text",
             "content": "温馨提示：您的课程即将到期，老学员续费可享受年度最低折扣，还能免费赠送全套教辅资料。"},

            # 竞赛课程收费咨询
            {"msg_id": "msg_kf_011", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-02-12 13:40:00"), "origin": 3,
             "msgtype": "text", "content": "数学竞赛培优课程一期多少钱？包含模考和赛前辅导吗？"},
            {"msg_id": "msg_kf_012", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-02-12 13:45:00"), "origin": 5,
             "msgtype": "text",
             "content": "竞赛培优一期3280元，全程包含知识点精讲、专项刷题、全真模考、赛前心态辅导，结业附赠竞赛备考手册。"},

            # 线上录播课功能咨询
            {"msg_id": "msg_kf_013", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-03-10 18:10:00"), "origin": 3,
             "msgtype": "text", "content": "购买的录播课可以无限次回放吗？有效期多久？"},
            {"msg_id": "msg_kf_014", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-03-10 18:15:00"), "origin": 5,
             "msgtype": "text",
             "content": "所有录播课有效期365天，支持无限次回放，配套课件、习题、答案解析均可免费下载，还有专属社群答疑。"},

            # 新初一衔接班咨询
            {"msg_id": "msg_kf_015", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-08-06 09:20:00"), "origin": 5,
             "msgtype": "text", "content": "家长您好，2025新初一衔接精品班仅剩8个名额，提前报名可享新生专属优惠！"},
            {"msg_id": "msg_kf_016", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-08-06 14:50:00"), "origin": 3,
             "msgtype": "text", "content": "衔接班主要学习哪些内容？会不会提前学初中课本知识？"},
            {"msg_id": "msg_kf_017", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-08-06 14:55:00"), "origin": 5,
             "msgtype": "text",
             "content": "课程包含小学知识查漏补缺、初中重难点预习、学习方法适配、思维模式转换，循序渐进帮孩子适应初中学习节奏。"},

            # 发票开具咨询
            {"msg_id": "msg_kf_018", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-04-12 10:30:00"), "origin": 3,
             "msgtype": "text", "content": "购课费用可以开具教育服务费发票吗？抬头可以开公司名称吗？"},
            {"msg_id": "msg_kf_019", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-04-12 10:35:00"), "origin": 5,
             "msgtype": "text",
             "content": "可以的！支持开具教育咨询服务费正规发票，个人、公司抬头均可，开票后1-3个工作日发送电子版发票。"},

            # 课程延期咨询
            {"msg_id": "msg_kf_020", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-05-20 08:00:00"), "origin": 5,
             "msgtype": "text", "content": "您的剩余课程课时即将到期，可申请免费延期保留课时！"},
            {"msg_id": "msg_kf_021", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-05-20 08:05:00"), "origin": 3,
             "msgtype": "text", "content": "可以延期多久？需要额外收费吗？"},
            {"msg_id": "msg_kf_022", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-05-20 08:10:00"), "origin": 5,
             "msgtype": "text", "content": "老学员可免费延期3个月，无任何额外费用，我这边直接帮您登记延期。"},

            # 课程真伪与师资核验咨询
            {"msg_id": "msg_kf_023", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-10 19:15:00"), "origin": 3,
             "msgtype": "text", "content": "你们的授课老师资质可以核验吗？有没有教师资格证？"},
            {"msg_id": "msg_kf_024", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-10 19:20:00"), "origin": 5,
             "msgtype": "text",
             "content": "所有授课老师均持有正规教师资格证，资质可公示核验，同时具备多年Cs教学经验，资质文件可随时查看。"},

            # 老学员专属活动咨询
            {"msg_id": "msg_kf_025", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-18 11:30:00"), "origin": 5,
             "msgtype": "text", "content": "老学员专属转介绍活动开启，成功推荐新学员报名，双方均可获得课时赠送！"},
            {"msg_id": "msg_kf_026", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-18 15:25:00"), "origin": 3,
             "msgtype": "text", "content": "你们的线下课程和线上课程教学内容一样吗？哪个效果更好？"},
            {"msg_id": "msg_kf_027", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-18 15:30:00"), "origin": 5,
             "msgtype": "text",
             "content": "教学大纲、知识点内容完全统一，线下课侧重互动监督，线上课灵活便捷，可根据孩子学习习惯选择。"},

            # 期末冲刺课程咨询
            {"msg_id": "msg_kf_028", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-20 12:40:00"), "origin": 3,
             "msgtype": "text", "content": "小学六年级期末冲刺课程主要讲什么？适合基础薄弱的孩子吗？"},
            {"msg_id": "msg_kf_029", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-06-20 12:45:00"), "origin": 5,
             "msgtype": "text",
             "content": "课程主打基础巩固、重难点突破、真题刷题、应试技巧讲解，分层教学，完全适合基础薄弱学员快速提分。"},

            # 初中培优课程推荐
            {"msg_id": "msg_kf_030", "external_id": DEMO_EXTERNAL_ID, "open_kfid": "kf001",
             "servicer_userid": DEMO_USER_ID, "msg_time": datetime2timestamp("2025-08-08 11:00:00"), "origin": 3,
             "msgtype": "text", "content": "新初一有没有长期培优课程推荐？想让孩子稳步提升成绩。"}
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
                logger.info(f"创建微信客服消息: {msg['msg_id']}")
            except Exception as e:
                logger.warning(f"微信客服消息 {msg['msg_id']} 可能已存在: {e}")
        
        logger.info(f"聊天记录初始化完成，共处理 {len(wxkf_messages)} 条消息")
    
    def init_orders(self):
        """初始化订单记录"""
        logger.info("正在初始化订单记录...")
        

        # {"union_id": "union_001","order_id": "order_001","open_id": "open_001","order_status": 100,"order_products": ["企业级解决方案"],"order_price": 2999.99,"order_create_time": base_create_time, "order_raw_info": {"memo": ""}}
        # 微信小店订单记录
        orders = [
            {"union_id": "union001", "order_id": "WX20240908", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2024-09-08 10:00:00"), "order_price": 4980.00,
             "order_products": ["六年级小升初全科冲刺小班课（12课时）"],
             "order_raw_info": {"memo": "孩子数学英语薄弱，针对性培优"}},
            {"union_id": "union001", "order_id": "WX20240928", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2024-09-28 10:00:00"), "order_price": 3280.00,
             "order_products": ["国庆7天全科集训营"], "order_raw_info": {"memo": "假期集中查漏补缺，提升应试能力"}},
            {"union_id": "union001", "order_id": "WX20241015", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2024-10-15 10:00:00"), "order_price": 6800.00,
             "order_products": ["英语一对一专项私教（20课时）"],
             "order_raw_info": {"memo": "矫正英语偏科，提升听说读写能力"}},
            {"union_id": "union001", "order_id": "WX20241120", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2024-11-20 10:00:00"), "order_price": 198.00,
             "order_products": ["2024小升初全国真题汇编全套"], "order_raw_info": {"memo": "日常刷题练习，积累题型经验"}},
            {"union_id": "union001", "order_id": "WX20250110", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2025-01-10 10:00:00"), "order_price": 3680.00,
             "order_products": ["寒假小升初冲刺集训营"], "order_raw_info": {"memo": "寒假弯道超车，冲刺重点初中"}},
            {"union_id": "union001", "order_id": "WX20250218", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2025-02-18 10:00:00"), "order_price": 3280.00,
             "order_products": ["小学数学竞赛培优专项课程"], "order_raw_info": {"memo": "培养竞赛思维，备战数学邀请赛"}},
            {"union_id": "union001", "order_id": "WX20250425", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2025-04-25 10:00:00"), "order_price": 2580.00,
             "order_products": ["小学语文阅读写作提升课程"], "order_raw_info": {"memo": "提升语文素养，攻克作文难点"}},
            {"union_id": "union001", "order_id": "WX20250622", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2025-06-22 10:00:00"), "order_price": 1680.00,
             "order_products": ["期末全科冲刺刷题班（8课时）"],
             "order_raw_info": {"memo": "期末考前突击，巩固核心知识点"}},
            {"union_id": "union001", "order_id": "WX20250810", "open_id": "open001", "order_status": 100,
             "order_create_time": datetime2timestamp("2025-08-10 10:00:00"), "order_price": 4280.00,
             "order_products": ["新初一学科衔接精品小班课"],
             "order_raw_info": {"memo": "平稳过渡小学初中学段，提前预习初中知识"}},
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
    initializer = DemoDataInitializer(CS_SERVER_URL, CS_USER_ID, CS_USER_PASSWORD)
    #initializer.init_tags()
    #initializer.init_user_and_customer()
    #initializer.init_wxqy_messages()
    #initializer.init_wxkf_messages()
    #initializer.init_orders()
    initializer.init_all_data()
    

if __name__ == "__main__":
    # 运行初始化脚本
    main()