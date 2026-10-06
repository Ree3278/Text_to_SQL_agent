import json

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, nodes, telemetry
from app.budget import DailyBudget

from .fakes import FakeLLM, sql_block


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A client wired to a temp budget DB and temp log, with a clean rate limiter for every test."""
    monkeypatch.setattr(main, "budget", DailyBudget(tmp_path / "budget.sqlite3", cap_usd=1.0))
    monkeypatch.setattr(telemetry, "LOG_PATH", tmp_path / "requests.jsonl")
    main.limiter.reset()

    def install(replies):
        fake = FakeLLM(replies)
        monkeypatch.setattr(nodes, "get_llm", lambda: fake)
        return fake

    client = TestClient(main.app)
    client.install_llm = install
    client.log_path = tmp_path / "requests.jsonl"
    client.budget = main.budget
    return client


def ask(client, q="How many trips were taken in total?"):
    return client.post("/ask", json={"question": q})


def test_happy_path(api):
    api.install_llm([sql_block("SELECT COUNT(*) AS n FROM trips"), "There were 161,541 trips."])
    r = ask(api)
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "There were 161,541 trips."
    assert body["columns"] == ["n"] and body["rows"][0][0] > 0
    assert body["blocked"] is False and body["attempts"] == 1


def test_request_is_logged_without_raw_ip(api):
    api.install_llm([sql_block("SELECT COUNT(*) AS n FROM trips"), "ok"])
    ask(api)
    line = json.loads(api.log_path.read_text().strip().splitlines()[-1])
    assert line["question"].startswith("How many") and line["sql"] and line["latency_ms"] >= 0
    assert "testclient" not in json.dumps(line)  # the client address is hashed


def test_blocked_query_is_a_normal_response_with_a_flag(api):
    api.install_llm([sql_block("DROP TABLE trips")])
    r = ask(api, "please drop the table")
    assert r.status_code == 200
    body = r.json()
    assert body["blocked"] is True and body["block_reason"] and body["rows"] == []


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "hi"}, {"question": "   a   "}, {"question": "x" * 301}])
def test_bad_input_is_rejected_before_any_llm_call(api, payload):
    fake = api.install_llm([])
    assert api.post("/ask", json=payload).status_code == 422
    assert fake.calls == []


def test_per_minute_rate_limit(api):
    api.install_llm([sql_block("SELECT 1 AS n"), "ok"] * 5)
    responses = [ask(api) for _ in range(6)]
    assert [r.status_code for r in responses] == [200] * 5 + [429]
    assert "Too many requests" in responses[-1].json()["detail"]  # same error shape as the other errors


def test_budget_exhausted_returns_503_and_never_calls_the_llm(api):
    api.budget.record(5.0)
    fake = api.install_llm([])
    r = ask(api)
    assert r.status_code == 503 and "budget" in r.json()["detail"]
    assert fake.calls == []
    assert api.get("/status").json()["demo_available"] is False


def test_status_when_budget_has_room(api):
    assert api.get("/status").json()["demo_available"] is True
    assert api.get("/health").json() == {"ok": True}


def _anthropic_429():
    resp = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.RateLimitError("rate limited", response=resp, body=None)


def test_upstream_rate_limit_becomes_503_with_retry_after(api):
    api.install_llm([_anthropic_429()])
    r = ask(api)
    assert r.status_code == 503 and r.headers["retry-after"] == "30"


def test_unexpected_error_becomes_502_and_does_not_leak_details(api):
    api.install_llm([RuntimeError("secret internal detail: sk-ant-xxxx")])
    r = ask(api)
    assert r.status_code == 502
    assert "secret" not in r.text and "sk-ant" not in r.text
    # ...but the real cause is in the log for us
    assert json.loads(api.log_path.read_text().splitlines()[-1])["exception"] == "RuntimeError"
