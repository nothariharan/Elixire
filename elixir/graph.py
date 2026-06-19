from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from state import ElixirState

from nodes.guard import guard_node
from nodes.emergency import emergency_node
from nodes.triage import triage_node
from nodes.research import research_node
from nodes.synthesis import synthesis_node
from nodes.verification import verification_node
from nodes.action import action_node


def _guard_router(state):
    """Route after guard: valid input → emergency check, invalid → END."""
    return "emergency" if state.get("is_valid", True) else END


def _emergency_router(state):
    """Route after emergency: emergency detected → END, otherwise → triage."""
    return END if state.get("emergency_flag") else "triage"


def _triage_router(state):
    if state.get("follow_up_questions"):
        return "interrupt"
    if state.get("triage_confidence", 0) >= 0.99:
        return "synthesis"
    return "research"


def build_graph(checkpointer: MemorySaver):
    g = StateGraph(ElixirState)
    g.add_node("guard", guard_node)
    g.add_node("emergency", emergency_node)
    g.add_node("triage", triage_node)
    g.add_node("research", research_node)
    g.add_node("synthesis", synthesis_node)
    g.add_node("verification", verification_node)
    g.add_node("action", action_node)

    g.set_entry_point("guard")
    g.add_conditional_edges("guard", _guard_router)
    g.add_conditional_edges("emergency", _emergency_router)
    g.add_conditional_edges("triage", _triage_router, {
        "interrupt": END,
        "synthesis": "synthesis",
        "research": "research",
    })
    g.add_edge("research", "synthesis")
    g.add_edge("synthesis", "verification")
    g.add_edge("verification", "action")
    g.add_edge("action", END)

    return g.compile(checkpointer=checkpointer, interrupt_before=[])
