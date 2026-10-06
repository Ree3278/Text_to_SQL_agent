"""Layer 3 on its own: even if the guard were bypassed entirely, DuckDB must refuse."""
import time
from pathlib import Path

import duckdb
import pytest

from app.db import run_query


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE trips",
        "DELETE FROM trips",
        "INSERT INTO stations VALUES (999, 'x', 'Bronx', 0, 0, 1)",
        "CREATE TABLE evil (x INT)",
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT * FROM glob('/*')",
        "SET enable_external_access = true",   # config is locked
        "COPY (SELECT 1) TO '/tmp/should_not_exist.csv'",
    ],
)
def test_database_refuses_without_the_guard(sql):
    with pytest.raises(duckdb.Error):
        run_query(sql)
    assert not Path("/tmp/should_not_exist.csv").exists()


def test_row_cap():
    r = run_query("SELECT * FROM trips", max_rows=5)
    assert len(r.rows) == 5 and r.truncated


def test_no_truncation_flag_when_small():
    r = run_query("SELECT * FROM stations", max_rows=1000)
    assert len(r.rows) == 62 and not r.truncated


def test_timeout_interrupts_runaway_query():
    start = time.perf_counter()
    with pytest.raises(duckdb.Error):
        run_query("SELECT count(*) FROM range(100000000000)", timeout_s=0.5)
    assert time.perf_counter() - start < 5
