#!/usr/bin/env python3
"""
Lightweight regression checks for hypothesis ledger + frozen Schwab-shaped fixtures.

No live API calls. Safe for CI alongside pytest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
FIXTURES = SKILL_DIR / "tests" / "fixtures"
sys.path.insert(0, str(SKILL_DIR))


def _load_fixture(name: str) -> dict:
    path = FIXTURES / name
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    from data_health import parse_quote_age_seconds, parse_quote_epoch_ms
    from hypothesis_ledger import fingerprint_from_mapping, record_from_signal

    q = _load_fixture("schwab_quote_aapl.json")
    assert q.get("lastPrice") == 198.5
    assert parse_quote_epoch_ms(q) is not None
    assert parse_quote_age_seconds(q) is not None

    ph = _load_fixture("schwab_pricehistory_spy.json")
    assert isinstance(ph.get("candles"), list) and len(ph["candles"]) >= 1
    for c in ph["candles"]:
        for k in ("open", "high", "low", "close", "volume", "datetime"):
            assert k in c

    diag = _load_fixture("scanner_diagnostics_sample.json")
    assert diag.get("data_quality") in {"ok", "degraded", "stale", "conflict"}

    fp1 = fingerprint_from_mapping({"z": 1, "a": 2})
    fp2 = fingerprint_from_mapping({"a": 2, "z": 1})
    assert fp1 == fp2, "fingerprint should be order-stable"

    sig = {
        "ticker": "TEST",
        "price": 10.0,
        "sma_50": 9.0,
        "sma_200": 8.0,
        "signal_score": 55.0,
        "mirofish_conviction": 40,
        "sector_etf": "XLK",
    }
    rec = record_from_signal(sig, skill_dir=SKILL_DIR)
    assert rec["source"] == "signal_scanner"
    assert rec["prediction"]["direction"] == "long"
    assert rec["input_fingerprint"].startswith("sha256:")

    print("PASS: validate_hypothesis_chain checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
