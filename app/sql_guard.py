"""SQL guard: decides whether LLM-written SQL may touch the database.

Defense in depth - this is layer 2 of 3:
  1. (prompt)    the model is told to write one read-only query        -> a request, not a control
  2. (this file) parse the SQL into an AST and enforce a policy        -> catches bad SQL BEFORE it runs
  3. (db.py)     read-only DuckDB, external access off, config locked  -> still holds if layer 2 has a bug

Why an AST and not regex? Regex sees text; a parser sees structure. "DROP" inside a string
literal or a comment is harmless and a parser knows that; `dRoP/**/TABLE` is dangerous and
a parser knows that too. We also execute the *re-rendered* SQL, not the raw text, so what we
validated is exactly what runs.

Policy:
  - exactly ONE statement, and it must be a SELECT (or UNION of SELECTs, CTEs allowed)
  - no write / DDL / admin nodes anywhere in the tree
  - tables: allowlist only (CTE names are fine). No table functions, no file paths, no other schemas
  - functions: unknown functions must be on an allowlist (blocks getenv, current_setting, ...)
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = frozenset({"stations", "trips", "daily_weather"})
ALLOWED_SCHEMAS = frozenset({"", "main"})
MAX_SQL_CHARS = 4000

_SET_OP = getattr(exp, "SetOperation", exp.Union)
_ALLOWED_ROOTS = (exp.Select, _SET_OP)

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Copy", "Pragma",
    "Set", "Attach", "Detach", "Use", "Merge", "Into", "Install", "Transaction", "Commit",
    "Rollback", "TruncateTable", "Grant", "Revoke",
)
_FORBIDDEN_NODES = tuple(c for n in _FORBIDDEN_NODE_NAMES if (c := getattr(exp, n, None)) is not None)

# sqlglot knows many functions as typed nodes (Count, Sum, TimeToStr, ...) - those are all pure
# computation. Anything it does NOT recognise comes back as `Anonymous`, and that is where
# dangerous DuckDB-specific functions hide, so Anonymous functions must be on this allowlist.
ALLOWED_ANONYMOUS_FUNCS = frozenset("""
    dayname monthname dayofweek dayofmonth dayofyear isodow isoyear weekofyear week month year
    quarter hour minute second epoch epoch_ms date_part datepart date_diff datediff date_sub datesub
    date_add make_date make_timestamp make_time last_day time_bucket strptime strftime to_timestamp
    date_trunc age
    abs round ceil ceiling floor sqrt pow power exp ln log log10 log2 sign greatest least mod cbrt
    degrees radians sin cos tan asin acos atan atan2 pi trunc truncate
    median mode quantile quantile_cont quantile_disc approx_quantile approx_count_distinct
    stddev stddev_pop stddev_samp variance var_pop var_samp corr covar_pop covar_samp regr_slope
    arg_max arg_min max_by min_by any_value first last list array_agg string_agg bool_and bool_or
    count_if countif histogram
    lower upper length len trim ltrim rtrim substr substring left right concat concat_ws replace
    regexp_matches regexp_extract regexp_replace starts_with ends_with contains position strpos
    split_part string_split lpad rpad format printf reverse
    coalesce nullif ifnull if
    list_aggregate list_sort list_contains list_distinct array_length list_value
    rank dense_rank percent_rank cume_dist ntile row_number lag lead first_value last_value nth_value
""".split())


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None = None      # canonical SQL to execute; only set when ok
    code: str | None = None     # machine-readable reason (useful for metrics)
    reason: str | None = None   # human-readable reason


def _reject(code: str, reason: str) -> GuardResult:
    return GuardResult(ok=False, sql=None, code=code, reason=reason)


def validate(sql: str) -> GuardResult:
    if not sql or not sql.strip():
        return _reject("empty", "No SQL was produced.")
    if len(sql) > MAX_SQL_CHARS:
        return _reject("too_long", f"SQL is longer than {MAX_SQL_CHARS} characters.")

    try:
        parsed = sqlglot.parse(sql, read="duckdb")
    except Exception as e:  # ParseError, TokenError, anything: unparseable means untrusted
        return _reject("parse_error", f"Could not parse the SQL ({type(e).__name__}).")

    # A trailing ';' (esp. followed by a comment) can show up as an extra empty statement; ignore those.
    statements = [s for s in parsed if s is not None and not isinstance(s, getattr(exp, "Semicolon", ()))]
    if len(statements) != 1:
        return _reject("multi_statement", f"Exactly one statement is allowed, got {len(statements)}.")
    tree = statements[0]

    if not isinstance(tree, _ALLOWED_ROOTS):
        return _reject("not_select", f"Only SELECT queries are allowed (got {type(tree).__name__.upper()}).")

    bad = tree.find(*_FORBIDDEN_NODES)
    if bad is not None:
        return _reject("forbidden_node", f"{type(bad).__name__.upper()} is not allowed in a query.")

    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return _reject("table", "Table functions (like read_csv or glob) are not allowed.")
        if table.catalog or table.db.lower() not in ALLOWED_SCHEMAS:
            return _reject("table", "Only tables in the main schema are allowed.")
        name = table.name.lower()
        if name not in ALLOWED_TABLES and not (name in cte_names and not table.db):
            return _reject("table", f"Table '{table.name}' is not allowed.")

    for fn in tree.find_all(exp.Func):
        if isinstance(fn, exp.Anonymous):
            fname = fn.name.lower()
            if fname not in ALLOWED_ANONYMOUS_FUNCS:
                return _reject("function", f"Function '{fname}' is not allowed.")
        elif isinstance(fn, getattr(exp, "ReadCSV", ())):
            return _reject("function", "File-reading functions are not allowed.")

    return GuardResult(ok=True, sql=tree.sql(dialect="duckdb"))
