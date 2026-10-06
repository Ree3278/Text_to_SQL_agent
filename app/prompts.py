"""Prompt text. Kept separate from logic so prompt changes show up cleanly in git diffs
(weekend 3: we will measure each prompt change with the eval set)."""
from __future__ import annotations

from functools import lru_cache

from . import db

# Human-written meaning for columns. Types come from the DB; semantics come from us.
COLUMN_NOTES = {
    "stations.station_id": "primary key",
    "stations.borough": "Manhattan | Brooklyn | Queens | Bronx",
    "stations.capacity": "number of docks",
    "trips.ride_id": "primary key",
    "trips.rideable_type": "bike type",
    "trips.started_at": "trip start (local time)",
    "trips.ended_at": "trip end (local time)",
    "trips.start_station_id": "FK -> stations.station_id",
    "trips.end_station_id": "FK -> stations.station_id",
    "trips.member_casual": "rider type",
    "trips.trip_duration_sec": "trip length in seconds",
    "daily_weather.date": "one row per calendar day",
    "daily_weather.avg_temp_c": "average temperature, Celsius",
    "daily_weather.precipitation_mm": "total precipitation in mm (0 = dry day)",
}

# Columns whose exact string values the model must know.
ENUM_COLUMNS = [("trips", "member_casual"), ("trips", "rideable_type"), ("stations", "borough")]


@lru_cache(maxsize=1)
def build_schema_text() -> str:
    lines = []
    for table, cols in db.describe_schema().items():
        lines.append(f"TABLE {table}")
        for col, dtype in cols:
            note = COLUMN_NOTES.get(f"{table}.{col}")
            lines.append(f"  - {col} {dtype}" + (f"  -- {note}" if note else ""))
    lines.append("")
    lines.append("Exact category values:")
    for table, col in ENUM_COLUMNS:
        lines.append(f"  - {table}.{col}: " + ", ".join(repr(v) for v in db.distinct_values(table, col)))
    lo, hi = db.date_range("trips", "started_at")
    lines.append(f"Trips cover {lo} to {hi}.")
    return "\n".join(lines)


SQL_SYSTEM = """You are an expert DuckDB SQL analyst for a bike-share database.
Write ONE read-only DuckDB SQL query that answers the user's question.

Rules:
- Reply with only the SQL inside a ```sql fenced block. No explanation.
- Use only the tables and columns listed in the schema.
- Use DuckDB syntax (date_trunc, extract, strftime, dayofweek, ...).
- Give result columns clear aliases. Add ORDER BY for rankings.
- If the question cannot be answered from this schema, return: SELECT 'unanswerable' AS note

Schema:
{schema}
"""

SUMMARY_SYSTEM = """You answer questions about bike-share data using ONLY the query result you are given.
Reply in 1-3 plain sentences. Include the key numbers with units.
If the result is empty, say no matching data was found. If the result is marked truncated, say it is a partial list.
Do not invent numbers that are not in the result."""
