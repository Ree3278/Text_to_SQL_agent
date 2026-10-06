"""DuckDB access: read-only connection, row cap, timeout, schema introspection."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import duckdb

from .config import DB_MAX_MEMORY, DB_PATH, DB_THREADS, MAX_ROWS, QUERY_TIMEOUT_S


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool  # True if more than max_rows rows existed


def connect() -> duckdb.DuckDBPyConnection:
    """The ONLY way the app opens the database (layer 3 of the defense).

    - read_only: no statement can modify the file, even if one slipped past the guard
    - enable_external_access=False: DuckDB itself refuses to read/write files or reach the network
      (so read_csv('/etc/passwd') and COPY ... TO fail even if the guard had a bug)
    - lock_configuration: a query can't flip those settings back with SET
    - max_memory / threads: a heavy query can't take the whole machine
    """
    return duckdb.connect(
        str(DB_PATH),
        read_only=True,
        config={
            "enable_external_access": False,
            "lock_configuration": True,
            "max_memory": DB_MAX_MEMORY,
            "threads": DB_THREADS,
        },
    )


def run_query(sql: str, *, max_rows: int = MAX_ROWS, timeout_s: float = QUERY_TIMEOUT_S) -> QueryResult:
    """Run SQL on a fresh READ-ONLY connection.

    - read_only=True: even if a bad statement slipped past validation, the file cannot be modified.
    - Timer + interrupt(): a runaway query (e.g. accidental cross join) is cancelled.
    - fetchmany(max_rows + 1): we never pull an unbounded result into memory.
    A connection per call is cheap in DuckDB and keeps this thread-safe for FastAPI later.
    """
    con = connect()
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)
    finally:
        timer.cancel()
        con.close()


def describe_schema() -> dict[str, list[tuple[str, str]]]:
    """{table: [(column, type), ...]} straight from the database, so the prompt never drifts from reality."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
        ).fetchall()
    finally:
        con.close()
    schema: dict[str, list[tuple[str, str]]] = {}
    for table, col, dtype in rows:
        schema.setdefault(table, []).append((col, dtype))
    return schema


def distinct_values(table: str, column: str, limit: int = 12) -> list[str]:
    """Low-cardinality values (e.g. member_casual) so the LLM uses the exact strings in WHERE clauses."""
    con = connect()
    try:
        return [r[0] for r in con.execute(f"SELECT DISTINCT {column} FROM {table} ORDER BY 1 LIMIT {limit}").fetchall()]
    finally:
        con.close()


def date_range(table: str, column: str) -> tuple[str, str]:
    con = connect()
    try:
        lo, hi = con.execute(f"SELECT min({column}), max({column}) FROM {table}").fetchone()
        return str(lo), str(hi)
    finally:
        con.close()
