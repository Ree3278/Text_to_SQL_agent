import pytest

from app import nodes


@pytest.mark.parametrize(
    "model,expected_temperature",
    [
        ("claude-haiku-4-5-20251001", 0),   # allowed: we pin it to 0 for repeatable SQL
        ("claude-sonnet-5-5", None),        # rejects non-default temperature: must be left unset
        ("claude-fable-5-1", None),
    ],
)
def test_temperature_only_set_where_the_model_allows_it(monkeypatch, model, expected_temperature):
    monkeypatch.setattr(nodes, "MODEL", model)
    nodes.get_llm.cache_clear()
    try:
        assert nodes.get_llm().temperature == expected_temperature
    finally:
        nodes.get_llm.cache_clear()
