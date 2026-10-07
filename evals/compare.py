"""Grade a model's answer by comparing RESULT SETS, not SQL text.

Two very different queries can be equally correct (JOIN vs subquery, COUNT(*) vs COUNT(ride_id)),
so comparing SQL strings would punish correct answers. Instead we run both queries and compare
what they return - the standard "execution accuracy" idea from text-to-SQL research.

Rules (deliberately strict about VALUES, lenient about PRESENTATION):
  - column names are ignored
  - column ORDER is ignored, and EXTRA columns in the model's answer are allowed
    (asking for "the busiest station" and also getting its trip count is not wrong)
  - row order is ignored unless the question is a ranking (order_matters=True)
  - integers must match EXACTLY (an off-by-one count is a wrong answer)
  - non-integer numbers match within a small tolerance (12.74 vs 12.7 is the same answer, rounded)
  - text is compared case-insensitively; dates and midnight timestamps are the same thing
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from itertools import product

REL_TOL = 5e-3   # 0.5% relative ...
ABS_TOL = 0.05   # ... or 0.05 absolute, whichever is larger
MAX_ASSIGNMENTS = 2000  # safety cap on column-matching combinations


def norm(v):
    """Put a database value into a canonical form so equal answers compare equal."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return int(v) if v.is_integer() else v  # 161541.0 is the integer 161541
    if isinstance(v, str):
        return v.strip().casefold()
    return v


def value_equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b  # counts must be exact
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _sort_key(v):
    if v is None:
        return (2, 0, "")
    if isinstance(v, str):
        return (1, 0, v)
    return (0, v, "")


def _seq_equal(a, b) -> bool:
    return len(a) == len(b) and all(value_equal(x, y) for x, y in zip(a, b))


def _column_equal(gold_col, pred_col, order_matters: bool) -> bool:
    if order_matters:
        return _seq_equal(gold_col, pred_col)
    return _seq_equal(sorted(gold_col, key=_sort_key), sorted(pred_col, key=_sort_key))


def _rows_equal(a_rows, b_rows, order_matters: bool) -> bool:
    if len(a_rows) != len(b_rows):
        return False
    if order_matters:
        return all(_seq_equal(x, y) for x, y in zip(a_rows, b_rows))
    unused = list(b_rows)
    for row in a_rows:
        for i, cand in enumerate(unused):
            if _seq_equal(row, cand):
                del unused[i]
                break
        else:
            return False
    return True


def results_match(pred_rows, gold_rows, order_matters: bool = False) -> tuple[bool, str]:
    """True if the model's result contains the gold result. Returns (ok, reason)."""
    gold = [tuple(norm(v) for v in r) for r in gold_rows]
    pred = [tuple(norm(v) for v in r) for r in pred_rows]

    if len(pred) != len(gold):
        return False, f"row count differs: got {len(pred)}, expected {len(gold)}"
    if not gold:
        return True, "ok"

    gold_cols = list(zip(*gold))
    pred_cols = list(zip(*pred))
    if len(pred_cols) < len(gold_cols):
        return False, f"too few columns: got {len(pred_cols)}, expected at least {len(gold_cols)}"

    # For each gold column, which model columns hold the same values?
    candidates = [
        [j for j, pc in enumerate(pred_cols) if _column_equal(gc, pc, order_matters)] for gc in gold_cols
    ]
    for k, c in enumerate(candidates):
        if not c:
            return False, f"no column in the answer matches expected column {k + 1}"

    tried = 0
    for assignment in product(*candidates):
        if len(set(assignment)) != len(assignment):
            continue  # two gold columns can't map to the same answer column
        tried += 1
        if tried > MAX_ASSIGNMENTS:
            break
        projected = [tuple(r[j] for j in assignment) for r in pred]
        if _rows_equal(projected, gold, order_matters):
            return True, "ok"
    return False, "values match column by column but not row by row"
