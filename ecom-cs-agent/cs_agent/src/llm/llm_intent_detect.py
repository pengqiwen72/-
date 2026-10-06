import json
from typing import List
from models.cs_models import Intent
from llm.llm_setting import chat_model as llm
from langchain_core.messages import AIMessage
from utils.agent_logger import get_logger

logger = get_logger("llm_intent_detect")


def llm_intent_detect(task_input: str, intents: List[Intent]) -> Intent:
    prompt = _intent_detection_instructions.format(
        task_input=task_input,
        intents=json.dumps([intent.model_dump() for intent in intents], ensure_ascii=False, indent=4),
        schema_json=Intent.get_schema_json(),
        example_json=Intent.get_example_json(),
    )

    logger.debug(f"prompt: {prompt}")

    generated_result: AIMessage = llm.invoke(prompt)

    logger.debug(f"generated_result: {generated_result}")
    
    if generated_result and generated_result.content:
        intent_json = generated_result.content  
    else:
        intent_json = "{}"
    
    logger.debug(f"intent_json: {intent_json}")
    
    try:
        intent = Intent.model_validate_json(intent_json)
    except Exception as e:
        logger.error(f"解析意图检测结果失败: {e}")
        intent = Intent()
    
    logger.info(f"intent: {intent.model_dump_json()}")

    return intent

_intent_detection_instructions = """
你是一个出色的面向公司内部销售人员的智能助手。
给你指令的都是公司内部销售人员，不是客户。
【客户相关信息是在后续的流程中从系统中加载进来的】

现在需要根据销售人员输入的任务信息，判断员工的意图。


以下是意图列表：
-------------------
{intents}
-------------------

你需要根据任务描述，返回意图 Intent 对象，后续流程会根据你返回的意图调用对应的流程。

【重要】你必须回复一个有效的JSON对象
【重要】请不要在JSON对象前后包含任何文本。也不要包含“```json”或者“```”这样的文本。
【重要】请不要在json对象中包含任何未定义的字段。

这里是Intent的数据结构定义：
-------------------
{schema_json}
-------------------

JSON 对象样例：
-------------------
{example_json}
-------------------

任务描述：
-------------------
{task_input}
-------------------

"""