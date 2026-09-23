from models.cs_models import CustomerTags, ChatHistory, KFChatHistory, OrderHistory, CustomerProfile
from llm.llm_setting import chat_model as llm
from langchain_core.messages import AIMessage
from utils.agent_logger import get_logger

logger = get_logger("llm_suggest_profile")

def llm_profile_suggest(chat_history: ChatHistory, kf_chat_history: KFChatHistory, order_history: OrderHistory, customer_tags: CustomerTags, customer_profile: CustomerProfile) -> CustomerProfile:

    prompt = _profile_instructions.format(
        chat_history=chat_history.model_dump_json(),
        kf_chat_history=kf_chat_history.model_dump_json(),
        order_history=order_history.model_dump_json(),
        customer_tags=customer_tags.model_dump_json(),
        customer_profile=customer_profile.model_dump_json(),
        schema_json=CustomerProfile.get_schema_json(),
        example_json=CustomerProfile.get_example_json(),
    )

    logger.debug(f"prompt: {prompt}")

    # 生成客户profile建议
    generated_result: AIMessage = llm.invoke(prompt)
    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        generated_profile_json = generated_result.content
    else:
        generated_profile_json = "{}"

    try:
        generated_profile = CustomerProfile.model_validate_json(generated_profile_json)
        logger.info(f"generated_profile: {generated_profile.model_dump_json()}")
        return generated_profile
    except Exception as e:
        logger.error(f"生成客户profile建议失败: {e}")
        return CustomerProfile()



_profile_instructions = """
你的任务是根据客户的相关信息，提取客户的profile信息：CustomerProfile。

这里是客户profile信息CustomerProfile的数据结构定义：
-------------------
{schema_json}
-------------------

json对象示例：
-------------------
{example_json}
-------------------


请根据下面的客户信息，生成CustomerProfile：

1、【重要】您必须回复一个有效JSON对象。
2、请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
3、JSON对象结构必须符合CustomerProfile的定义
4、内容包含你认为可以描述客户情况的所有信息。
5、旧profile中需要保留的信息，也更新到新的profile中，业务系统使用新profile时，会完整覆盖旧的profile。



示例目标字段：

1. 【家长姓名】:包括昵称、曾用名等可能相关的称呼。
2. 【家长年龄】: 精确到具体岁数，若提及大概年龄段也需注明。
3. 【家长性别】: 明确是男、女或其他表述。
4. 【家长职业】: 具体的工作类型，如包含多个职业可全部列出。
5. 【家长兴趣爱好】: 如具体的运动项目、阅读、育儿交流等。
6. 【婚姻状况】: 是否已婚、未婚、离异等。
7. 【家长教育程度】: 如高中、专科、本科、硕士等。
8. 【学生学段年级】: 如小学一年级、初中三年级、高中二年级等。
9. 【学生性别】: 明确学生男、女。
10. 【学生学习情况】: 如成绩优异、中等、基础薄弱、偏科、学霸、待提升等。
11. 【薄弱学科】: 语文、数学、英语、理化生、政史地等具体科目。
12. 【优势学科】: 语文、数学、英语、理化生、政史地等具体科目。
13. 【学习目标】: 小升初、中考、高考、校内提分、竞赛升学、素质提升等。
14. 【报课类型偏好】: 学科培优、冲刺班、一对一私教、小班课、集训营、素质课程、竞赛专项等。
15. 【上课形式偏好】: 线下面授、线上录播、线上直播、混合教学等。
16. 【上课时间偏好】: 周末班、周中晚班、寒暑假集训、白天时段、晚间时段等。
17. 【购课消费频率】: 首次购课、季度购课、学期购课、常年续费、临时短期报课等。
18. 【单次购课大致金额】: 填写具体金额区间或数值。
19. 【教辅资料采购习惯】: 高频采购、按需购买、极少购买、只买真题卷等。
20. 【购课/购资料渠道】: 线下门店、线上小程序、企业微信、客服下单、社群团购等。
21. 【家庭成员】: 子女数量、是否多孩家庭、是否老人同住陪读等。
22. 【家长育儿状态】: 全职陪读、双职工无暇看管、隔代看护等。
23. 【家校沟通频率】: 频繁沟通、偶尔沟通、极少沟通。
24. 【服务关注点】: 师资力量、课后答疑、学情报告、补课服务、名额预留、课程延期等。
25. 【课程续费意愿】: 意愿强烈、观望、暂无续费想法。

下面是客户的数据：



这里是客户和销售人员近期的对话记录：
-------------------
{chat_history}
-------------------

这里是客户和客服近期的对话记录：
-------------------
{kf_chat_history}
-------------------

这里是客户近期的订单信息：
-------------------
{order_history}
-------------------

这里是客户的标签信息：
-------------------
{customer_tags}
-------------------

这里是客户之前的profile信息：
-------------------
{customer_profile}
-------------------

"""
