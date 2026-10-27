from models.ecom_models import CustomerTags, ChatHistory, KFChatHistory, OrderHistory, CustomerProfile
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
你的任务是根据会员（买家）的相关信息，提取会员的画像信息：CustomerProfile。

这里是会员画像信息CustomerProfile的数据结构定义：
-------------------
{schema_json}
-------------------

json对象示例：
-------------------
{example_json}
-------------------


请根据下面的会员信息，生成CustomerProfile：

1、【重要】您必须回复一个有效JSON对象。
2、请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
3、JSON对象结构必须符合CustomerProfile的定义
4、内容包含你认为可以描述会员情况的所有信息。
5、旧profile中需要保留的信息，也更新到新的profile中，业务系统使用新profile时，会完整覆盖旧的profile。



示例目标字段：

1. 【会员昵称】:包括昵称、常用称呼等可能相关的称呼。
2. 【年龄段】: 如25-30岁，若提及具体年龄也需注明。
3. 【性别】: 明确是男、女或其他表述。
4. 【职业/身份】: 如企业行政、教师、自由职业、全职家庭主妇等。
5. 【常居城市】: 从收货地址或对话中推断的城市。
6. 【兴趣爱好】: 如家居收纳、烘焙、健身、母婴育儿等。
7. 【家庭结构】: 是否已婚、子女数量、是否与老人同住等。
8. 【消费能力层级】: 高、中高、中等、价格导向等，结合客单价与品类判断。
9. 【价格敏感度】: 高度敏感（只买促销）、中等（会等大促、问价保）、不敏感（重品质）。
10. 【偏好品类】: 家纺、服饰、小家电、食品礼盒、母婴、美妆等具体品类。
11. 【常购品牌】: 具体品牌名称或品牌风格偏好。
12. 【尺码/规格偏好】: 服饰尺码、床品尺寸、家电规格等。
13. 【购买频次】: 首次购买、月均1单、月均多单、大促集中囤货等。
14. 【客单价区间】: 填写具体金额区间或数值。
15. 【下单渠道偏好】: 微信小店、直播间、企业微信导购代下单、小程序、线下门店等。
16. 【促销活动偏好】: 满减、会员券、秒杀、拼团、以旧换新、赠品等。
17. 【物流时效要求】: 是否在意发货与送达速度、是否接受预售。
18. 【售后服务倾向】: 理性沟通、情绪化、要求补偿、习惯差评等。
19. 【退换货倾向】: 高频退换、偶有退换、极少退换，以及主要退换原因。
20. 【会员等级与积分关注】: 是否关注会员价、积分兑换、等级权益。
21. 【内容与互动偏好】: 喜欢实拍细节图、短视频测评、图文种草、直播讲解等。
22. 【沟通风格偏好】: 直接高效、需要耐心解答、偏好文字或语音等。
23. 【决策关注点】: 品质、价格、售后保障、发货速度、口碑等核心决策因素。
24. 【复购与续卡意愿】: 意愿强烈、观望、暂无复购想法。
25. 【流失风险】: 高、中、低，并说明判断依据。

下面是会员的数据：



这里是会员和导购顾问近期的对话记录：
-------------------
{chat_history}
-------------------

这里是会员和售后客服近期的对话记录：
-------------------
{kf_chat_history}
-------------------

这里是会员近期的订单信息：
-------------------
{order_history}
-------------------

这里是会员的标签信息：
-------------------
{customer_tags}
-------------------

这里是会员之前的profile信息：
-------------------
{customer_profile}
-------------------

"""
