"""Graph nodes. Each takes the state and returns a PARTIAL state update."""
from __future__ import annotations

import re
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from . import db, sql_guard
from .config import LLM_MAX_RETRIES, LLM_TIMEOUT_S, MODEL, ROWS_FOR_SUMMARY
from .prompts import SQL_SYSTEM, SUMMARY_SYSTEM, build_schema_text
from .state import AgentState


@lru_cache(maxsize=1)
def get_llm() -> ChatAnthropic:
    # temperature=0: we want the most likely SQL, not creative SQL (also makes evals more repeatable)
    return ChatAnthropic(
        model=MODEL, temperature=0, max_tokens=1024, timeout=LLM_TIMEOUT_S, max_retries=LLM_MAX_RETRIES
    )


def _text(message) -> str:
    """AIMessage.content is a str or a list of content blocks depending on the model/version."""
    c = message.content
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c if isinstance(b, dict))


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else text).strip().rstrip(";").strip()


def generate_sql(state: AgentState) -> dict:
    messages = [
        SystemMessage(SQL_SYSTEM.format(schema=build_schema_text())),
        HumanMessage(state["question"]),
    ]
    # Retry: show the model its own failed query and the database's error message.
    if state.get("error") and state.get("sql"):
        messages += [
            AIMessage(f"```sql\n{state['sql']}\n```"),
            HumanMessage(
                f"That query failed with this DuckDB error:\n{state['error']}\n\n"
                "Fix it. Reply with only the corrected SQL in a ```sql block."
            ),
        ]
    reply = get_llm().invoke(messages)
    return {
        "sql": extract_sql(_text(reply)),
        "attempts": state.get("attempts", 0) + 1,
        "error": None,
    }


def validate_sql(state: AgentState) -> dict:
    """Policy check (see sql_guard.py). On success we swap in the canonical SQL - what we validated is what runs."""
    result = sql_guard.validate(state["sql"])
    if result.ok:
        return {"sql": result.sql, "guard_code": None, "guard_reason": None}
    return {"guard_code": result.code, "guard_reason": result.reason}


def refuse(state: AgentState) -> dict:
    """Policy violations get a refusal, never a retry: we don't coach the model to find a way around the guard."""
    return {
        "columns": [],
        "rows": [],
        "truncated": False,
        "answer": (
            "I can only answer questions by running read-only queries on the bike data, "
            f"so I blocked that query. ({state['guard_reason']})"
        ),
    }


def execute_sql(state: AgentState) -> dict:
    try:
        result = db.run_query(state["sql"])
    except Exception as e:  # DuckDB errors, timeouts, anything: record it; the graph decides whether to retry
        return {"error": f"{type(e).__name__}: {e}", "columns": [], "rows": [], "truncated": False}
    return {"error": None, "columns": result.columns, "rows": result.rows, "truncated": result.truncated}


def summarize(state: AgentState) -> dict:
    if state.get("error"):
        return {"answer": f"I couldn't run that query. {state['error']}"}
    shown = state["rows"][:ROWS_FOR_SUMMARY]
    table = "\n".join([",".join(state["columns"])] + [",".join(map(str, r)) for r in shown])
    note = "\n(result truncated)" if state.get("truncated") else ""
    reply = get_llm().invoke(
        [
            SystemMessage(SUMMARY_SYSTEM),
            HumanMessage(f"Question: {state['question']}\n\nSQL:\n{state['sql']}\n\nResult (CSV):\n{table}{note}"),
        ]
    )
    return {"answer": _text(reply).strip()}
