from langgraph.graph import START, END, StateGraph
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_state import SubChatSuggestionState
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_state import SubChatSuggestionInputState
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_state import SubChatSuggestionOutputState
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_node import generate_chat_suggestion_node
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_node import welcome_message_node
from graphs.cs_graph.sub_chat_suggestion.sub_chat_suggestion_node import NodeName
from graphs.cs_graph.cs_state import CsConfig

builder = StateGraph(
    state_schema=SubChatSuggestionState, 
    input_schema=SubChatSuggestionInputState,  
    output_schema=SubChatSuggestionOutputState, 
    config_schema=CsConfig
)

builder.add_node(NodeName.WELCOME_MESSAGE.value, welcome_message_node)
builder.add_node(NodeName.GENERATE_CHAT_SUGGESTION.value, generate_chat_suggestion_node)

builder.add_edge(START, NodeName.WELCOME_MESSAGE.value)
builder.add_edge(NodeName.WELCOME_MESSAGE.value, NodeName.GENERATE_CHAT_SUGGESTION.value)
builder.add_edge(NodeName.GENERATE_CHAT_SUGGESTION.value, END)

sub_chat_suggestion_graph = builder.compile()