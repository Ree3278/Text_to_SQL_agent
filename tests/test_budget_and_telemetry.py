from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app import telemetry
from app.budget import DailyBudget
from app.config import PRICING


def test_budget_blocks_once_cap_is_reached(tmp_path):
    b = DailyBudget(tmp_path / "b.sqlite3", cap_usd=0.10)
    assert b.has_room()
    b.record(0.06)
    assert b.has_room()
    b.record(0.06)
    assert not b.has_room()
    assert abs(b.spent_today() - 0.12) < 1e-9


def test_budget_persists_across_instances(tmp_path):
    """The whole point of SQLite: a restart (new process, new object) must not reset the counter."""
    DailyBudget(tmp_path / "b.sqlite3", cap_usd=0.10).record(0.50)
    assert not DailyBudget(tmp_path / "b.sqlite3", cap_usd=0.10).has_room()


def test_budget_resets_on_a_new_day(tmp_path):
    day = {"d": "2026-10-06"}
    b = DailyBudget(tmp_path / "b.sqlite3", cap_usd=0.10, today=lambda: day["d"])
    b.record(1.00)
    assert not b.has_room()
    day["d"] = "2026-10-07"
    assert b.has_room() and b.spent_today() == 0.0


def _llm_result(input_tokens, output_tokens):
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def test_usage_callback_sums_tokens_and_prices_them():
    cb = telemetry.UsageCallback(model="claude-haiku-4-5-20251001")
    cb.on_llm_end(_llm_result(1000, 500))
    cb.on_llm_end(_llm_result(2000, 100))  # e.g. the summarize call
    assert (cb.input_tokens, cb.output_tokens, cb.llm_calls) == (3000, 600, 2)
    in_p, out_p = PRICING["claude-haiku-4-5-20251001"]
    assert abs(cb.cost_usd - (3000 * in_p + 600 * out_p) / 1e6) < 1e-12


def test_unknown_model_is_priced_at_the_most_expensive_known_rate():
    most_expensive_out = max(p[1] for p in PRICING.values())
    assert telemetry.cost_usd("some-future-model", 0, 1_000_000) == most_expensive_out


def test_usage_callback_tolerates_missing_usage():
    cb = telemetry.UsageCallback()
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="no usage"))]]))
    assert cb.cost_usd == 0 and cb.llm_calls == 1


def test_ip_is_hashed_in_logs():
    h = telemetry.hash_ip("203.0.113.9")
    assert "203" not in h and len(h) == 10 and h == telemetry.hash_ip("203.0.113.9")
