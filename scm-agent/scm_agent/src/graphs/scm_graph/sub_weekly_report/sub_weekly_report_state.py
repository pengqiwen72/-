"""数据周报子图的 State 定义。"""

import operator
from enum import Enum
from typing_extensions import Annotated, List, TypedDict

from graphs.scm_graph.scm_state import ScmStateField
from models.scm_models import NodeResult, TaskResult, WeeklyReport


class SubWeeklyReportStateField(str, Enum):
    ''' 数据周报子图的字段名 '''

    TASK_INPUT    = ScmStateField.TASK_INPUT.value
    WEEKLY_REPORT = ScmStateField.WEEKLY_REPORT.value
    TASK_RESULT   = ScmStateField.TASK_RESULT.value
    NODE_RESULT   = ScmStateField.NODE_RESULT.value

    # 子图内部字段：只在 collect / aggregate / narrate 之间流转。
    # 名字必须以纯字符串字面量再写进下面的 TypedDict —— TypedDict 的键只能是
    # 字符串字面量，枚举成员在这里不会被识别成字段。
    ROWS_CURRENT  = "report_rows_current"
    ROWS_PREVIOUS = "report_rows_previous"
    WINDOW        = "report_window"


class SubWeeklyReportInputState(TypedDict):
    task_input: str


class SubWeeklyReportOutputState(TypedDict):
    task_result: TaskResult
    node_result: Annotated[List[NodeResult], operator.add]
    # ⚠️ 必须出现在 output_schema 里，否则子图结束时该字段会被丢弃。
    weekly_report: WeeklyReport


class SubWeeklyReportState(SubWeeklyReportInputState, SubWeeklyReportOutputState):
    ''' 数据周报子图 State（含只在子图内部流转的中间字段）'''

    # ⚠️ 这三个字段**刻意不放进 output_schema**。
    #
    # 它们装的是归一后的原始订单行（本周 + 上周两批），是纯粹的中间产物：
    # 父图拿到它们没有任何用处，却会跟着检查点一起被持久化 —— 一次周报就是
    # 上百条订单明细，写进检查点属于白白让每轮对话的存档变大。
    #
    # 但它们**必须**出现在 state_schema 里：LangGraph 只认 state_schema 声明过的
    # 通道，节点返回一个未声明的键会被判为非法更新。所以「内部可见、外部不可见」
    # 这个需求，正是 state_schema / output_schema 分离要解决的问题。
    report_rows_current: List[dict]
    report_rows_previous: List[dict]
    report_window: List[str]
