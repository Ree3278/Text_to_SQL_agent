"""The LangGraph workflow.

    START -> generate_sql -> validate_sql --(ok)-------> execute_sql --(ok)-----------------> summarize -> END
                 ^               |                          |
                 |               +--(policy violation)--> refuse -> END
                 |                                          |
                 +------(DB error AND attempts left)-------+

Two different failures get two different treatments on purpose:
  - policy violation (guard says no)  -> refuse. Retrying would just invite the model to probe the guard.
  - execution error (bad column name) -> retry once with the error message. This is a plain mistake the model can fix.
"""
from langgraph.graph import END, START, StateGraph

from .config import MAX_ATTEMPTS
from .nodes import execute_sql, generate_sql, refuse, summarize, validate_sql
from .state import AgentState


def route_after_validate(state: AgentState) -> str:
    return "refuse" if state.get("guard_reason") else "execute_sql"


def route_after_execute(state: AgentState) -> str:
    if state.get("error") and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "generate_sql"
    return "summarize"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_sql", validate_sql)
    g.add_node("refuse", refuse)
    g.add_node("execute_sql", execute_sql)
    g.add_node("summarize", summarize)

    g.add_edge(START, "generate_sql")
    g.add_edge("generate_sql", "validate_sql")
    g.add_conditional_edges("validate_sql", route_after_validate, {"execute_sql": "execute_sql", "refuse": "refuse"})
    g.add_conditional_edges("execute_sql", route_after_execute, {"generate_sql": "generate_sql", "summarize": "summarize"})
    g.add_edge("refuse", END)
    g.add_edge("summarize", END)
    return g.compile()


graph = build_graph()
