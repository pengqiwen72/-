
from models.scm_models import TagSetting, TagSuggestion, CustomerTags, ChatHistory, KFChatHistory, OrderHistory
from datetime import datetime
from llm.llm_setting import chat_model as llm
from langchain_core.messages import AIMessage
from utils.agent_logger import get_logger

logger = get_logger("llm_suggest_tag")

def llm_tag_suggest(tag_setting: TagSetting, customer_tags: CustomerTags, chat_history: ChatHistory, kf_chat_history: KFChatHistory, order_history: OrderHistory, current_time = None) -> TagSuggestion:
    # TagSuggestion
    prompt =  _tags_suggest_instructions.format(
        tag_setting = tag_setting.model_dump_json(),
        customer_tags = customer_tags.model_dump_json(),
        chat_history = chat_history.model_dump_json(),
        kf_chat_history = kf_chat_history.model_dump_json(),
        order_history = order_history.model_dump_json(),
        current_time = current_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        schema_json = TagSuggestion.get_schema_json(),
        example_json = TagSuggestion.get_example_json(),
    )

    logger.debug(f"prompt: {prompt}")
    # 生成客户标签建议
    generated_result: AIMessage = llm.invoke(prompt)

    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        generated_tag_suggestion_json = generated_result.content
    else:
        generated_tag_suggestion_json = "{}"

    try:
        generated_tag_suggestion = TagSuggestion.model_validate_json(generated_tag_suggestion_json)
        logger.info(f"generated_tag_suggestion: {generated_tag_suggestion.model_dump_json()}")
        return generated_tag_suggestion
    except Exception as e:
        logger.error(f"生成客户标签建议失败: {e}")
        return TagSuggestion()


_tags_suggest_instructions = """
你的任务是根据客户的相关信息，生成客户标签变更建议：TagSuggestion。

这里是标签变更建议TagSuggestion的数据结构定义：
-------------------
{schema_json}
-------------------

json对象示例：
-------------------
{example_json}
-------------------

请根据下面的客户信息，生成TagSuggestion，具体要求：

1、【重要】您必须回复一个有效JSON对象
2、请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
3、json对象结构必须符合TagSuggestion的定义。其中tag_id必须是tag_setting中定义的标签ID（tag_id）。
4、请不要在json对象中包含任何未定义的字段。
5、每个要添加或者删除的标签，都要有明确的添加原因或者删除原因。
6、请不要添加或者删除未在tag_setting中定义的标签ID（tag_id）。
7、【重要】标签建议必须基于客户数据中的具体证据（对话原文或订单事实），不要凭常识推测。

下面是客户的数据，请你根据这些数据，生成TagSuggestion：

1、这是系统定义的所有标签（tag_setting），所生成的建议标签ID（tag_id）必须是tag_setting中定义的标签ID（tag_id）。所添加或者删除的标签，必须有明确的添加原因或者删除原因。
-------------------
{tag_setting}
-------------------

2、这是客户的现有的标签信息：
-------------------
{customer_tags}
-------------------

3、这是客户和客服坐席近期的对话记录：
-------------------
{chat_history}
-------------------

4、这是客户和客服近期的对话记录：
-------------------
{kf_chat_history}
-------------------

5、这是客户近期的订单信息：
-------------------
{order_history}
-------------------


"""
