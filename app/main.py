"""HTTP API.   Run:  uvicorn app.main:app --reload

Request path:   per-IP rate limit -> daily budget gate -> LangGraph agent -> log + record spend

Every protection here exists because this endpoint spends real money per call:
  - rate limit (per IP, per minute and per day)  -> one client can't drain the budget
  - daily budget cap (persistent)                -> all clients together can't drain it
  - input length cap + pydantic validation       -> a huge prompt can't inflate token cost
  - SDK retries with backoff + upstream error mapping -> Anthropic 429s/outages become clean responses
"""
from __future__ import annotations

import os
import time

import anthropic
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import sqlglot
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import telemetry
from .budget import DailyBudget
from .config import (
    DAILY_BUDGET_USD,
    MAX_QUESTION_CHARS,
    MODEL,
    RATE_LIMIT_PER_DAY,
    RATE_LIMIT_PER_MINUTE,
    RESPONSE_MAX_ROWS,
    ROOT,
    STATE_DIR,
)
from .graph import graph

UI_DIR = ROOT / "ui"
UI_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _pretty(sql: str) -> str:
    """Multi-line SQL for display only. Execution always uses the guard's canonical SQL."""
    try:
        return sqlglot.transpile(sql, read="duckdb", write="duckdb", pretty=True)[0]
    except Exception:
        return sql


def client_key(request: Request) -> str:
    """Who is this client? Behind Fly's proxy every connection comes from the proxy, so we need the
    header Fly sets. We only trust it when running on Fly (FLY_APP_NAME is set there): anywhere else a
    client could forge it and dodge the rate limit."""
    if os.getenv("FLY_APP_NAME"):
        ip = request.headers.get("fly-client-ip")
        if ip:
            return ip
    return get_remote_address(request)


limiter = Limiter(key_func=client_key)  # in-memory counters: fine for short windows on one machine
budget = DailyBudget(STATE_DIR / "budget.sqlite3", DAILY_BUDGET_USD)

app = FastAPI(title="Ask the Bike Data", version="0.2.0")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def _rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # Same {"detail": ...} shape as every other error, so a UI needs only one code path.
    return JSONResponse({"detail": f"Too many requests ({exc.detail}). Please slow down."}, status_code=429)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=MAX_QUESTION_CHARS)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("question is too short")
        return v


class AskResponse(BaseModel):
    answer: str
    sql: str
    columns: list[str]
    rows: list[list]
    truncated: bool
    attempts: int
    blocked: bool
    failed: bool = False
    block_reason: str | None = None
    latency_ms: int
    input_tokens: int
    output_tokens: int


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html", media_type="text/html", headers=UI_HEADERS)


app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/status")
def status() -> dict:
    """Lets a UI show 'the demo is resting' instead of failing on click."""
    return {"demo_available": budget.has_room(), "model": MODEL}


@app.post("/ask", response_model=AskResponse)
@limiter.limit(RATE_LIMIT_PER_MINUTE)
@limiter.limit(RATE_LIMIT_PER_DAY)
def ask(request: Request, body: AskRequest) -> AskResponse:
    # Plain `def` (not async): the agent makes blocking network/DB calls, so FastAPI runs this in a
    # worker thread and the event loop stays free.
    if not budget.has_room():
        raise HTTPException(503, "The demo has used up today's budget and is resting. Please come back tomorrow.")

    usage = telemetry.UsageCallback()
    t0 = time.perf_counter()
    error_name = None
    out: dict = {}
    try:
        out = graph.invoke({"question": body.question}, config={"callbacks": [usage]})
    except Exception as e:  # never leak internals to the client; keep the details in the log
        error_name = type(e).__name__
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        budget.record(usage.cost_usd)  # failed requests may still have cost tokens
        telemetry.log_request(
            {
                "ip": telemetry.hash_ip(client_key(request)),
                "question": body.question,
                "sql": out.get("sql"),
                "attempts": out.get("attempts"),
                "guard_code": out.get("guard_code"),
                "exec_error": out.get("error"),
                "exception": error_name,
                "latency_ms": latency_ms,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": round(usage.cost_usd, 6),
                "model": MODEL,
            }
        )

    if error_name:
        # The SDK already retried with backoff; if we're here, the provider is genuinely unhappy.
        if error_name in ("RateLimitError", "OverloadedError", "InternalServerError", "APIConnectionError", "APITimeoutError"):
            raise HTTPException(503, "The AI provider is busy right now. Please try again in a moment.", headers={"Retry-After": "30"})
        raise HTTPException(502, "Something went wrong while answering that. Please try again.")

    return AskResponse(
        answer=out["answer"],
        sql=_pretty(out.get("sql", "")) if out.get("sql") else "",
        columns=out.get("columns", []),
        rows=[list(r) for r in out.get("rows", [])[:RESPONSE_MAX_ROWS]],
        truncated=bool(out.get("truncated")) or len(out.get("rows", [])) > RESPONSE_MAX_ROWS,
        attempts=out.get("attempts", 1),
        blocked=bool(out.get("guard_reason")),
        failed=bool(out.get("error")),
        block_reason=out.get("guard_reason"),
        latency_ms=latency_ms,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
