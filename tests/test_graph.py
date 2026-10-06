"""Graph routing tests with a scripted fake LLM: no API calls, no cost, fully deterministic."""
import pytest
from langchain_core.messages import AIMessage

from app import nodes
from app.graph import graph


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.replies.pop(0))


@pytest.fixture
def fake_llm(monkeypatch):
    def install(replies):
        fake = FakeLLM(replies)
        monkeypatch.setattr(nodes, "get_llm", lambda: fake)
        return fake

    return install


def sql_block(sql):
    return f"```sql\n{sql}\n```"


def test_happy_path_makes_two_llm_calls(fake_llm):
    llm = fake_llm([sql_block("SELECT COUNT(*) AS n FROM trips"), "There were many trips."])
    out = graph.invoke({"question": "how many trips?"})
    assert out["attempts"] == 1 and not out.get("error") and not out.get("guard_reason")
    assert out["rows"][0][0] > 0
    assert out["answer"] == "There were many trips."
    assert len(llm.calls) == 2  # generate + summarize


def test_db_error_triggers_one_retry_with_the_error_message(fake_llm):
    llm = fake_llm(
        [
            sql_block("SELECT no_such_column FROM trips"),
            sql_block("SELECT COUNT(*) AS n FROM trips"),
            "Fixed it.",
        ]
    )
    out = graph.invoke({"question": "how many trips?"})
    assert out["attempts"] == 2 and out["error"] is None
    assert out["rows"][0][0] > 0
    retry_prompt = llm.calls[1]  # the second generate call
    assert "no_such_column" in retry_prompt[-1].content  # the model was shown the DB error
    assert "no_such_column" in retry_prompt[-2].content  # ...and its own failed query


def test_retries_are_capped(fake_llm):
    llm = fake_llm([sql_block("SELECT bad1 FROM trips"), sql_block("SELECT bad2 FROM trips")])
    out = graph.invoke({"question": "q"})
    assert out["attempts"] == 2 and out["error"]
    assert out["answer"].startswith("I couldn't run that query")
    assert len(llm.calls) == 2  # no third try, and no summarize call on failure


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE trips",
        "SELECT 1; DROP TABLE trips",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT getenv('ANTHROPIC_API_KEY')",
    ],
)
def test_policy_violation_is_refused_without_retry(fake_llm, bad_sql):
    llm = fake_llm([sql_block(bad_sql)])
    out = graph.invoke({"question": "attack"})
    assert out["guard_reason"] and out["guard_code"]
    assert out["answer"].startswith("I can only answer")
    assert out["attempts"] == 1
    assert len(llm.calls) == 1  # one generate call; no retry, no summarize
    assert out["rows"] == []
