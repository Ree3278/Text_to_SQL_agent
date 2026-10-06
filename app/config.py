"""Central settings. Everything tunable lives here so it is easy to find and test."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # reads ANTHROPIC_API_KEY (and LangSmith vars if set)

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "bike.duckdb"))
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")

MAX_ROWS = 200          # rows returned to the app/LLM; more are cut off and flagged
QUERY_TIMEOUT_S = 10    # a runaway query is interrupted after this long
ROWS_FOR_SUMMARY = 30   # how many result rows the summarizer LLM gets to see
MAX_ATTEMPTS = 2        # total SQL generations per question (1 first try + 1 retry on a DB error)

DB_MAX_MEMORY = "512MB"  # DuckDB memory cap (also keeps us inside a small Fly.io machine)
DB_THREADS = 2
