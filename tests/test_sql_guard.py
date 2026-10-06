import pytest

from app.db import run_query
from app.sql_guard import validate

# Queries a well-behaved model should be able to write. A false refusal here is a BUG
# (it would show up as lost accuracy in the eval).
ALLOWED = [
    "SELECT COUNT(*) FROM trips",
    "select * from stations limit 5",
    "SELECT s.borough, COUNT(*) FROM trips t JOIN stations s ON t.start_station_id = s.station_id GROUP BY 1 ORDER BY 2 DESC",
    "WITH daily AS (SELECT CAST(started_at AS DATE) AS d, COUNT(*) AS n FROM trips GROUP BY 1) SELECT AVG(n) FROM daily",
    "SELECT member_casual, rideable_type, ROUND(AVG(trip_duration_sec)/60.0, 2) FROM trips GROUP BY ALL",
    "SELECT strftime(started_at, '%Y-%m') AS ym, COUNT(*) FROM trips GROUP BY 1 ORDER BY 1",
    "SELECT date_trunc('month', started_at) AS m, COUNT(*) FROM trips GROUP BY 1",
    "SELECT dayname(started_at) AS dow, COUNT(*) FROM trips GROUP BY 1",
    "SELECT EXTRACT(hour FROM started_at) AS h, COUNT(*) FROM trips GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
    "SELECT station_id, RANK() OVER (ORDER BY capacity DESC) FROM stations",
    "SELECT 'unanswerable' AS note",
    "SELECT * FROM main.trips LIMIT 1",
    "SELECT * FROM trips LIMIT 1;",
    "SELECT * FROM trips LIMIT 1; -- trailing comment",
    "SELECT * FROM trips UNION ALL SELECT * FROM trips",
    "SELECT median(trip_duration_sec) FROM trips",
    "SELECT quantile_cont(trip_duration_sec, 0.95) FROM trips",
    "SELECT * FROM (SELECT * FROM stations) s",
    "SELECT w.date, w.precipitation_mm FROM daily_weather w WHERE w.precipitation_mm > 1",
    "SELECT CASE WHEN precipitation_mm > 1 THEN 'rainy' ELSE 'dry' END, COUNT(*) FROM daily_weather GROUP BY 1",
    "SELECT 'DROP TABLE trips' AS harmless_string",       # DROP inside a string literal is fine
    "SELECT * FROM trips LIMIT 1 /* DROP TABLE trips */",  # ...and inside a comment
]

# (sql, expected reason code) - code None means "any rejection is fine"
BLOCKED = [
    # statement type
    ("DROP TABLE trips", "not_select"),
    ("DELETE FROM trips", "not_select"),
    ("INSERT INTO stations VALUES (999, 'x', 'Bronx', 0, 0, 1)", "not_select"),
    ("UPDATE trips SET member_casual = 'x'", "not_select"),
    ("CREATE TABLE evil AS SELECT * FROM trips", "not_select"),
    ("ALTER TABLE trips ADD COLUMN x INT", "not_select"),
    ("COPY trips TO '/tmp/out.csv'", "not_select"),
    ("COPY (SELECT * FROM trips) TO '/tmp/out.csv'", "not_select"),
    ("ATTACH 'other.db' AS other", "not_select"),
    ("PRAGMA database_list", "not_select"),
    ("INSTALL httpfs", "not_select"),
    ("LOAD httpfs", "not_select"),
    ("SET enable_external_access = true", "not_select"),
    ("EXPORT DATABASE '/tmp/x'", None),
    ("WITH x AS (DELETE FROM trips RETURNING *) SELECT * FROM x", None),
    # multi-statement / obfuscation
    ("SELECT 1; DROP TABLE trips", "multi_statement"),
    ("SELECT * FROM trips; SELECT * FROM stations", "multi_statement"),
    ("SELECT 1 /* x */; DROP TABLE trips", "multi_statement"),
    ("sElEcT 1; dRoP tAbLe trips", "multi_statement"),
    # files / table functions
    ("SELECT * FROM read_csv('/etc/passwd')", "table"),
    ("SELECT * FROM read_csv_auto('/etc/passwd')", "table"),
    ("SELECT * FROM read_parquet('data/parquet/trips.parquet')", "table"),
    ("SELECT * FROM '/etc/passwd'", "table"),
    ("SELECT * FROM 'data/parquet/trips.parquet'", "table"),
    ("SELECT * FROM glob('/*')", "table"),
    ("SELECT * FROM duckdb_tables()", "table"),
    ("SELECT * FROM duckdb_settings()", "table"),
    ("SELECT * FROM trips t, read_json('/etc/passwd') j", "table"),
    ("SELECT (SELECT 1 FROM read_text('/etc/passwd'))", "table"),
    ("SELECT * FROM trips WHERE trip_duration_sec > (SELECT count(*) FROM read_csv('/etc/passwd'))", "table"),
    # other tables / schemas
    ("SELECT * FROM information_schema.tables", "table"),
    ("SELECT * FROM unknown_table", "table"),
    ("SELECT * FROM mydb.main.trips", "table"),
    # scalar functions that leak environment / settings
    ("SELECT getenv('ANTHROPIC_API_KEY')", "function"),
    ("SELECT current_setting('home_directory')", "function"),
    # junk
    ("", "empty"),
    ("   ", "empty"),
    ("this is not sql", None),
    ("SELECT FROM WHERE", None),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    r = validate(sql)
    assert r.ok, f"false refusal: {r.code}: {r.reason}"
    assert r.sql and r.code is None


@pytest.mark.parametrize("sql,code", BLOCKED)
def test_blocked(sql, code):
    r = validate(sql)
    assert not r.ok, f"guard let this through: {sql!r}"
    assert r.sql is None  # nothing executable is ever returned for a rejected query
    if code:
        assert r.code == code, f"expected {code}, got {r.code} ({r.reason})"


def test_too_long_is_rejected():
    assert validate("SELECT " + "1," * 3000 + "1").code == "too_long"


# The guard returns re-rendered SQL, and that is what we execute. Prove the round trip does not
# change results (queries here have a total order or a single row, so they are deterministic).
ROUNDTRIP = [
    "SELECT COUNT(*) AS n FROM trips",
    "SELECT member_casual, COUNT(*) AS n FROM trips GROUP BY 1 ORDER BY 1",
    "SELECT strftime(started_at, '%Y-%m') AS ym, COUNT(*) AS n FROM trips GROUP BY 1 ORDER BY 1",
    "SELECT date_trunc('month', started_at) AS m, COUNT(*) AS n FROM trips GROUP BY 1 ORDER BY 1",
    "SELECT dayname(started_at) AS d, COUNT(*) AS n FROM trips GROUP BY 1 ORDER BY 1",
    "SELECT EXTRACT(hour FROM started_at) AS h, COUNT(*) AS n FROM trips GROUP BY 1 ORDER BY 1",
    "SELECT ROUND(AVG(trip_duration_sec) / 60.0, 2) AS m FROM trips",
    "WITH d AS (SELECT CAST(started_at AS DATE) AS day, COUNT(*) AS n FROM trips GROUP BY 1) SELECT ROUND(AVG(n), 3) FROM d",
    "SELECT s.borough, COUNT(*) AS n FROM trips t JOIN stations s ON s.station_id = t.start_station_id GROUP BY 1 ORDER BY 1",
    "SELECT median(trip_duration_sec) AS med, quantile_cont(trip_duration_sec, 0.95) AS p95 FROM trips",
    "SELECT date, precipitation_mm FROM daily_weather WHERE precipitation_mm > 20 ORDER BY date",
]


@pytest.mark.parametrize("sql", ROUNDTRIP)
def test_canonical_sql_returns_same_rows(sql):
    r = validate(sql)
    assert r.ok, r.reason
    assert run_query(r.sql).rows == run_query(sql).rows
