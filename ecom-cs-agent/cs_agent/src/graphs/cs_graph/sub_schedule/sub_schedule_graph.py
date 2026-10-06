from langgraph.graph import START, END, StateGraph
from graphs.cs_graph.sub_schedule.sub_schedule_state import SubScheduleState, SubScheduleInputState, SubScheduleOutputState
from graphs.cs_graph.sub_schedule.sub_schedule_node import generate_schedule_node, create_schedule_node, welcome_message_node
from graphs.cs_graph.sub_schedule.sub_schedule_node import NodeName
from graphs.cs_graph.cs_state import CsConfig

builder = StateGraph(state_schema=SubScheduleState, input_schema=SubScheduleInputState, output_schema=SubScheduleOutputState, config_schema=CsConfig)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message_node)
builder.add_node(NodeName.GENERATE_SCHEDULE.value, generate_schedule_node)
builder.add_node(NodeName.CREATE_SCHEDULE.value, create_schedule_node)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.GENERATE_SCHEDULE.value)
builder.add_edge(NodeName.GENERATE_SCHEDULE.value, NodeName.CREATE_SCHEDULE.value)
builder.add_edge(NodeName.CREATE_SCHEDULE.value, END)

sub_schedule_graph = builder.compile()