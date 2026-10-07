from langgraph.graph import START, END, StateGraph
from graphs.scm_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_state import SubKFChatSuggestionState
from graphs.scm_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_state import SubKFChatSuggestionInputState
from graphs.scm_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_state import SubKFChatSuggestionOutputState
from graphs.scm_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_node import generate_kf_chat_suggestion_node
from graphs.scm_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_node import welcome_message_node
from graphs.scm_graph.sub_kf_chat_suggestion.sub_kf_chat_suggestion_node import NodeName
from graphs.scm_graph.scm_state import ScmConfig

builder = StateGraph(
    state_schema=SubKFChatSuggestionState, 
    input_schema=SubKFChatSuggestionInputState, 
    output_schema=SubKFChatSuggestionOutputState, 
    config_schema=ScmConfig
)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message_node)
builder.add_node(NodeName.GENERATE_KF_CHAT_SUGGESTION.value, generate_kf_chat_suggestion_node)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.GENERATE_KF_CHAT_SUGGESTION.value)
builder.add_edge(NodeName.GENERATE_KF_CHAT_SUGGESTION.value, END)

sub_kf_chat_suggestion_graph = builder.compile()