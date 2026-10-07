"""Guards against a broken eval set: a bad gold query would silently corrupt every score."""
from pathlib import Path

import pytest
import yaml

from app.db import run_query
from app.sql_guard import validate

QUESTIONS = yaml.safe_load((Path(__file__).parent.parent / "evals" / "questions.yaml").read_text())
WITH_GOLD = [q for q in QUESTIONS if "gold_sql" in q]


def golds(q):
    g = q["gold_sql"]
    return g if isinstance(g, list) else [g]


def test_ids_unique_and_fields_present():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids))
    for q in QUESTIONS:
        assert q["split"] in ("dev", "test")
        assert q["question"] and q["category"]
        assert ("gold_sql" in q) != (q.get("expect") == "refusal"), f"{q['id']}: need gold_sql XOR expect: refusal"


def test_both_splits_exist_and_cover_every_category():
    for split in ("dev", "test"):
        assert sum(q["split"] == split for q in QUESTIONS) >= 8


@pytest.mark.parametrize("q", WITH_GOLD, ids=lambda q: q["id"])
def test_gold_queries_run_return_rows_and_pass_the_guard(q):
    for sql in golds(q):
        assert validate(sql).ok, f"the guard rejects the gold query for {q['id']} (allowlist too strict?)"
        assert run_query(sql).rows, f"{q['id']}: gold returns no rows (a question with an empty answer is a bad test)"
