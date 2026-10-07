from models.scm_models import CustomerTags, ChatHistory, KFChatHistory, OrderHistory, CustomerProfile
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

    # 生成客户画像建议
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
        logger.error(f"生成客户画像建议失败: {e}")
        return CustomerProfile()



_profile_instructions = """
你的任务是根据客户的相关信息，提取客户的profile信息：CustomerProfile。

这里是客户画像信息CustomerProfile的数据结构定义：
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



示例目标字段（四组共 22 项，按此口径输出）：

【基础属性】
1. 【客户姓名】: 包括昵称、备注名、曾用称呼等可能相关的称法。
2. 【客户性别】: 明确是男、女或其他表述。
3. 【年龄段】: 填写区间，如 25-30 岁、30-35 岁；有确切年龄则写明。
4. 【所在城市】: 精确到市/区，如浙江省杭州市西湖区。
5. 【职业】: 具体工作类型，如包含多个可全部列出。
6. 【家庭成员】: 婚育情况、子女数量、是否与老人同住等。

【消费特征】
7. 【消费能力】: 高 / 中高 / 中等 / 偏低，并注明判断依据（如是否在意价格）。
8. 【客单价区间】: 填写具体金额区间，由订单金额推算。
9. 【购买频次】: 如月均 1 单、月均 2-3 单、季度少量下单、大促集中下单等。
10. 【累计消费金额】: 由订单历史求和得出，写明数值。
11. 【下单渠道】: 企业微信私聊、小程序商城、直播间、客服代下单、社群团购等。
12. 【支付方式偏好】: 微信支付、支付宝、银行卡、先用后付等。
13. 【常用收货地址】: 具体到可投递粒度，并注明收货时间特征（工作日/周末可收）。

【偏好】
14. 【品类偏好】: 如家居日用、数码配件、杯壶、灯具等，注明关注方向。
15. 【品牌倾向】: 是否认品牌、偏好有质保承诺的品牌、对新品牌接受度。
16. 【价格敏感度】: 高 / 中等 / 中等偏低 / 低，并说明其更看重什么（价格 vs 品质 vs 售后）。
17. 【促销参与度】: 是否对满减、赠品、大促敏感。

【服务敏感度】
18. 【退换货倾向】: 高 / 中 / 低；是否倾向于优先换货而非退款；是否明确主张权益。
19. 【历史售后记录】: 时间 + 商品 + 事由 + 处理结果，如「近 30 天 1 次：电热毯控制器失灵，已全额退款」。
20. 【对售后时效要求】: 高 / 中 / 低，并注明期望的明确时限（如 24 小时内给出结论）。
21. 【沟通风格】: 直接/委婉、是否需要反复确认、偏好文字还是语音、是否要求书面结论。
22. 【关注点】: 客户反复追问的点，如发货时效、质保范围、破损赔付流程、参数真实性。



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
