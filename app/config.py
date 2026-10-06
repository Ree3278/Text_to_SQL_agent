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

# ---- API / production constraints -------------------------------------------------------------
STATE_DIR = Path(os.getenv("STATE_DIR", ROOT / "state"))   # request log + budget DB (a Fly volume in prod)
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "0.50"))
RATE_LIMIT_PER_MINUTE = os.getenv("RATE_LIMIT_PER_MINUTE", "5/minute")  # slowapi syntax, per client IP
RATE_LIMIT_PER_DAY = os.getenv("RATE_LIMIT_PER_DAY", "60/day")
MAX_QUESTION_CHARS = 300
RESPONSE_MAX_ROWS = 50   # rows returned by the API (the DB layer may fetch up to MAX_ROWS)

LLM_TIMEOUT_S = 30
LLM_MAX_RETRIES = 3      # the Anthropic SDK retries 429/5xx/connection errors with exponential backoff

# USD per million tokens (input, output). Source: platform.claude.com/docs/en/about-claude/pricing,
# checked 2026-10-06. Re-check before trusting the budget numbers - prices change.
PRICING = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
}
