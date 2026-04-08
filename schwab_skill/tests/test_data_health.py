import json
from pathlib import Path

from data_health import merge_operator_payload, parse_quote_epoch_ms


def test_fixture_quote_has_parseable_timestamp() -> None:
    root = Path(__file__).resolve().parent
    q = json.loads((root / "fixtures" / "schwab_quote_aapl.json").read_text(encoding="utf-8"))
    assert parse_quote_epoch_ms(q) is not None


def test_merge_operator_payload_defaults() -> None:
    assert merge_operator_payload(None)["data_quality"] == "unknown"
