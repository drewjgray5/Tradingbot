#!/usr/bin/env python3
"""
Lightweight validation checks for SEC enrichment rollout.

Checks:
1) SEC snapshot path returns structured payload and writes cache.
2) Full report EDGAR builder is resilient on SEC failures.
3) Scanner diagnostics summary aggregates SEC counters.
4) Filing compare utilities return stable compare payload schema.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    import full_report
    import sec_enrichment
    import sec_filing_compare
    import signal_scanner as scanner

    # Validation 1: snapshot path shape + cache write (live SEC call may succeed/fail).
    snap = sec_enrichment.fetch_sec_snapshot(
        "AAPL",
        skill_dir=SKILL_DIR,
        user_agent="TradingBot test@example.com",
        cache_hours=12.0,
        enabled=True,
    )
    required_keys = {"ok", "ticker", "recent_filings", "risk_tag", "recent_8k", "error"}
    if not required_keys.issubset(set(snap.keys())):
        print(f"FAIL: SEC snapshot missing keys: {sorted(required_keys - set(snap.keys()))}")
        return 1
    cache_file = SKILL_DIR / sec_enrichment.SEC_CACHE_FILE
    if not cache_file.exists():
        print("FAIL: SEC cache file was not created")
        return 1

    # Validation 2: report fallback behavior when SEC fetch fails.
    with patch(
        "sec_enrichment.fetch_sec_snapshot",
        return_value={
            "ok": False,
            "ticker": "AAPL",
            "cik": "",
            "recent_filings": [],
            "risk_tag": "unknown",
            "risk_reasons": [],
            "recent_8k": False,
            "filing_recency_days": None,
            "from_cache": False,
            "error": "synthetic SEC failure",
        },
    ):
        edgar = full_report._build_edgar("AAPL")
        if "synthetic SEC failure" not in (edgar.error or ""):
            print("FAIL: _build_edgar did not surface SEC failure warning")
            return 1

    # Validation 3: scanner SEC diagnostics are tracked in quality summary.
    diag = {
        "sec_tagged_signals": 3,
        "sec_recent_8k_count": 1,
        "sec_high_risk_tag_count": 1,
        "sec_data_failures": 0,
    }
    scanner._record_quality_snapshot(SKILL_DIR, diag, [{"ticker": "AAPL", "signal_score": 55.0}])
    summary = scanner.get_signal_quality_summary(skill_dir=SKILL_DIR, days=1)
    if summary.get("diagnostics", {}).get("sec_tagged_signals", 0) < 1:
        print("FAIL: SEC diagnostics were not aggregated in quality summary")
        return 1

    # Validation 4: compare payload shape from synthetic analyses.
    left = {
        "key_themes": ["Revenue growth improved.", "Liquidity remains strong."],
        "risk_terms": ["litigation", "default"],
        "guidance_signal": "negative",
        "kpi_signals": {
            "revenue_mentions": ["revenue: $12 billion"],
            "profit_mentions": ["net income: $2 billion"],
            "cashflow_mentions": [],
            "debt_mentions": ["debt: $5 billion"],
            "liquidity_mentions": ["cash and cash equivalents: $8 billion"],
        },
    }
    right = {
        "key_themes": ["Revenue growth improved.", "Operating margin stable."],
        "risk_terms": ["litigation"],
        "guidance_signal": "positive",
        "kpi_signals": {
            "revenue_mentions": ["revenue: $10 billion"],
            "profit_mentions": [],
            "cashflow_mentions": [],
            "debt_mentions": [],
            "liquidity_mentions": ["cash and cash equivalents: $7 billion"],
        },
    }
    cmp_payload = sec_filing_compare.compare_analyses(
        left,
        right,
        mode="ticker_vs_ticker",
        left_label="AAA",
        right_label="BBB",
    )
    expected_compare_keys = {"ok", "mode", "similarities", "differences", "metric_deltas", "investor_takeaway"}
    if not expected_compare_keys.issubset(set(cmp_payload.keys())):
        print("FAIL: SEC compare payload schema mismatch")
        return 1

    print("PASS: SEC enrichment validation checks succeeded")
    print(f"  live_snapshot_ok={bool(snap.get('ok'))}")
    print(f"  cache_file={cache_file.name}")
    print(f"  sec_tagged_signals={summary.get('diagnostics', {}).get('sec_tagged_signals', 0)}")
    print(f"  sec_compare_similarity_count={len(cmp_payload.get('similarities', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
