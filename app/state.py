from __future__ import annotations

from typing import Optional, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    sql: str
    attempts: int                # how many times generate_sql has run
    guard_code: Optional[str]    # set when sql_guard rejected the query
    guard_reason: Optional[str]
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    error: Optional[str]         # set when DuckDB failed to run the query
    answer: str
