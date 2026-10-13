from llm.llm_setting import chat_model as llm
from langchain_core.messages import AIMessage
from utils.agent_logger import get_logger
from models.scm_models import JustTalkOutput

logger = get_logger("llm_just_talk")

def llm_just_talk(input: str) -> JustTalkOutput:
    prompt = _just_talk_instructions.format(
        input=input,
        schema_json=JustTalkOutput.get_schema_json(),
        example_json=JustTalkOutput.get_example_json(),
    )
    logger.debug(f"prompt: {prompt}")

    generated_result: AIMessage = llm.invoke(prompt)
    logger.debug(f"generated_result: {generated_result}")

    if generated_result and generated_result.content:
        just_talk_json = generated_result.content
    else:
        just_talk_json = "{}"

    logger.debug(f"just_talk_json: {just_talk_json}")

    try:
        just_talk = JustTalkOutput.model_validate_json(just_talk_json)
    except Exception as e:
        logger.error(f"解析JustTalkOutput失败: {e}")
        just_talk = JustTalkOutput()

    logger.info(f"just_talk: {just_talk.model_dump_json()}")

    return just_talk


_just_talk_instructions = """
你是「供应链采购仓储智能助手」，服务对象是公司内部的采购员、库管与质检员。
不属于采购/收货/库存/供应商/物料制度这几类业务的问题，也可以给出有帮助的回答或建议。

下面是你需要回答的问题，你需要回答这个问题，并按照 JustTalkOutput 数据结构定义返回JSON。

【注意】请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
【注意】请不要在json对象中包含任何未定义的字段。

这里是JustTalkOutput的数据结构定义：
-------------------
{schema_json}
-------------------

json对象示例：
-------------------
{example_json}
-------------------

员工输入的问题：
-------------------
{input}
-------------------

"""