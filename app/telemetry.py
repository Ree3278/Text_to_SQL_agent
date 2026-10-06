"""Per-request token/cost accounting and the JSONL request log.

How tokens are counted: LangChain calls `on_llm_end` on every callback handler after each model
call. We pass one fresh handler per request (see main.py), so the totals belong to exactly one
question - and the handler is local to that request, so concurrent requests can't mix up.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .config import MODEL, PRICING, STATE_DIR

LOG_PATH = STATE_DIR / "requests.jsonl"
_log_lock = threading.Lock()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    # Unknown model -> assume the most expensive known price, so the budget errs on the safe side.
    in_price, out_price = PRICING.get(model) or max(PRICING.values())
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


class UsageCallback(BaseCallbackHandler):
    def __init__(self, model: str = MODEL):
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        self.llm_calls += 1
        for generations in response.generations:
            for gen in generations:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                self.input_tokens += usage.get("input_tokens", 0)
                self.output_tokens += usage.get("output_tokens", 0)

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.model, self.input_tokens, self.output_tokens)


def hash_ip(ip: str) -> str:
    """Log a stable pseudonym, not the raw IP: enough to spot one noisy client, not enough to identify a person."""
    return hashlib.sha256(ip.encode()).hexdigest()[:10]


def log_request(record: dict) -> None:
    """Append one JSON line. This file is the raw material for the weekend-3 metrics (p50/p95 latency, cost/query)."""
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    with _log_lock:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
