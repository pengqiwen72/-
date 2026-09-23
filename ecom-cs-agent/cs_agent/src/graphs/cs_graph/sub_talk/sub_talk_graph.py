import operator
from typing import TypedDict, Annotated, List
from enum import Enum
from langgraph.graph import StateGraph, START, END
from langchain_core.runnables import RunnableConfig
from utils.agent_logger import get_logger
from llm.llm_just_talk import llm_just_talk
from graphs.cs_graph.cs_state import CsStateField
from graphs.cs_graph.cs_state import CsConfig
from models.cs_models import TaskResult, NodeResult, JustTalkOutput

logger = get_logger("sub_talk_graph")

class SubTalkStateField(str, Enum):
    TASK_INPUT  = CsStateField.TASK_INPUT.value
    TASK_RESULT = CsStateField.TASK_RESULT.value
    NODE_RESULT = CsStateField.NODE_RESULT.value

class SubTalkInputState(TypedDict):
    task_input: str

class SubTalkOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]

# 使用多重继承，自动合并所有字段
class SubTalkState(SubTalkInputState, SubTalkOutputState):
    """
    完整的子图状态，包含输入和输出字段
    继承自 SubTagInputState 和 SubTagOutputState
    """
    pass


class NodeName(str, Enum):
    WELCOME_MESSAGE = "talk_welcome_node"
    JUST_TALK       = "talk_reply_node"

def welcome_message_node(state: SubTalkState, config: RunnableConfig):
    """欢迎消息节点"""
    
    logger.info("=== 欢迎消息 ===")
    
    return {
        SubTalkStateField.NODE_RESULT: [NodeResult(
            execute_node_name=NodeName.JUST_TALK.value,
            execute_result_code=0,
            execute_result_msg="正在为您生成回复，请稍等。",
            execute_exceptions=[]
        )]
    }

def just_talk_node(state: SubTalkState, config: RunnableConfig):
    """咨询回复节点"""
    
    logger.info("=== 咨询回复 ===")

    try:
        generated_just_talk_output: JustTalkOutput = llm_just_talk(
            input = state.get(SubTalkStateField.TASK_INPUT, "")
        )
        logger.info(f"generated_just_talk_output: {generated_just_talk_output}")

        if not generated_just_talk_output.just_talk_output:
            # ⚠️ 这里不能引用 `e`：本分支在 try 内，`e` 只在 except 里存在，
            #    走到这里必定 NameError，把「回复为空」变成「程序崩了」。
            return {
                SubTalkStateField.TASK_RESULT: TaskResult(
                    task_result="我好像没有理解你的意思",
                    task_result_explain="生成回复失败",
                    task_result_code=1
                ),
                SubTalkStateField.NODE_RESULT: [NodeResult(
                    execute_node_name=NodeName.JUST_TALK.value,
                    execute_result_code=1,
                    execute_result_msg="生成回复失败：模型返回内容为空",
                    execute_exceptions=["模型返回内容为空"]
                )]
            }

        return {
            SubTalkStateField.TASK_RESULT: TaskResult(
                task_result=generated_just_talk_output.just_talk_output,
                task_result_explain=f"生成回复成功",
                task_result_code=0
            ),
            SubTalkStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.JUST_TALK.value,
                execute_result_code=0,
                execute_result_msg="生成回复成功",
                execute_exceptions=[]
            )]
        }
    except Exception as e:
        logger.error(f"生成回复失败: {e}")
        return {
            SubTalkStateField.TASK_RESULT: TaskResult(
                task_result="抱歉，我好像有故障，无法回答你的问题",
                task_result_explain="解析失败",
                task_result_code=1
            ),
            SubTalkStateField.NODE_RESULT: [NodeResult(
                execute_node_name=NodeName.JUST_TALK.value,
                execute_result_code=1,
                execute_result_msg="解析失败",
                execute_exceptions=[f"解析结果失败: {e}"]
            )]  
        }

builder = StateGraph(state_schema=SubTalkState, input_schema=SubTalkInputState, output_schema=SubTalkOutputState, config_schema=CsConfig)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message_node)
builder.add_node(NodeName.JUST_TALK.value, just_talk_node)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.JUST_TALK.value)
builder.add_edge(NodeName.JUST_TALK.value, END)

sub_talk_graph = builder.compile()