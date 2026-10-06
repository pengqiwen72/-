from models.cs_models import ChatHistory, CustomerInfo, ScheduleSuggestion
from datetime import datetime
from llm.llm_setting import chat_model as llm
from langchain_core.messages import AIMessage
from utils.agent_logger import get_logger

logger = get_logger("llm_suggest_schedule")

def llm_schedule_suggest(customer_info: CustomerInfo, chat_history: ChatHistory, current_time = None) -> ScheduleSuggestion:
    """
    生成日程安排建议
    """
    
    prompt = _schedule_suggest_instructions.format(
        customer_info = customer_info.model_dump_json(),
        chat_history = chat_history.model_dump_json(),
        current_time = current_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        schema_json = ScheduleSuggestion.get_schema_json(),
        example_json = ScheduleSuggestion.get_example_json(),
    )
    logger.debug(f"prompt: {prompt}")
    # 生成日程安排建议
    generated_result: AIMessage = llm.invoke(prompt)

    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        generated_schedule_json = generated_result.content
    else:
        generated_schedule_json = "{}"

    try:
        generated_schedule = ScheduleSuggestion.model_validate_json(generated_schedule_json)
        logger.info(f"generated_schedule: {generated_schedule.model_dump_json()}")
        return generated_schedule
    except Exception as e:
        logger.error(f"生成日程安排建议失败: {e}")
        return ScheduleSuggestion()



_schedule_suggest_instructions = """
你是出色的日程安排助理，擅长对话中获得日程信息。

下面是近期客服坐席与客户的聊天记录，请根据聊天记录，提取日程安排建议 ScheduleSuggestion。

【注意】如果没有明确的日程信息，可以不用生成日程建议。
【注意】要根据消息中的发送者sender和接收者receiver，分清楚消息是客户发送的还是客服坐席发送的。
【注意】要根据消息中的发送时间msg_time，理解所讨论内容的时间背景，提取出日程的开始时间和持续时间，默认持续时间为30分钟。
【重要】如果对话中包含多个日程安排，请选择最重要或最紧急的一个日程进行建议。

请根据下面的 ScheduleSuggestion 数据结构定义生成 JSON：

数据结构 Schema：
-------------------
{schema_json}
-------------------

JSON 示例：
-------------------
{example_json}
-------------------


请根据下面的客户信息，生成ScheduleSuggestion，具体要求：

1、【重要】您必须回复一个有效JSON对象
2、请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
3、json对象结构必须符合ScheduleSuggestion的定义。
4、请不要在json对象中包含任何未定义的字段。
5、如果对话中包含多个日程安排，请选择最重要或最紧急的一个日程进行建议，不要返回多个日程建议



这里是客户和客服坐席近期的对话记录：
-------------------
{chat_history}
-------------------


这里是客户基础信息：
-------------------
{customer_info}
-------------------

这是当前真实世界的时间，供你参考：
-------------------
{current_time}
-------------------



"""
