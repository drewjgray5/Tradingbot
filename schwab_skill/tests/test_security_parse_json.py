from __future__ import annotations

from webapp.security import parse_json


def test_parse_json_accepts_already_parsed_dict() -> None:
    payload = {"ticker": "AAPL", "score": 88}
    assert parse_json(payload, {}) == payload


def test_parse_json_accepts_already_parsed_list() -> None:
    payload = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
    assert parse_json(payload, []) == payload


def test_parse_json_parses_string_json() -> None:
    raw = '{"ticker":"NVDA","score":91.2}'
    out = parse_json(raw, {})
    assert isinstance(out, dict)
    assert out["ticker"] == "NVDA"


def test_parse_json_returns_fallback_on_type_mismatch() -> None:
    assert parse_json({"k": "v"}, []) == []
    assert parse_json([1, 2], {}) == {}
