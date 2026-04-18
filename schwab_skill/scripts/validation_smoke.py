#!/usr/bin/env python3
"""
Deterministic smoke checks for scan/web/discord-adjacent flows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _check_demo_scan() -> tuple[bool, str]:
    from signal_scanner import scan_for_signals_detailed

    class _FakeAuth:
        def __init__(self, skill_dir: Path | str | None = None):
            self.skill_dir = Path(skill_dir or SKILL_DIR)

    fake_df = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1_000_000, 1_100_000],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )

    prev = os.environ.get("DEMO_SIGNAL")
    os.environ["DEMO_SIGNAL"] = "1"
    try:
        with (
            patch("schwab_auth.DualSchwabAuth", _FakeAuth),
            patch("market_data.get_daily_history", return_value=fake_df),
            patch("market_data.get_current_quote", return_value={"lastPrice": 222.22}),
        ):
            signals, diagnostics = scan_for_signals_detailed(skill_dir=SKILL_DIR)
    finally:
        if prev is None:
            os.environ.pop("DEMO_SIGNAL", None)
        else:
            os.environ["DEMO_SIGNAL"] = prev

    if not signals:
        return False, "demo scan returned no signals"
    first = signals[0]
    if not first.get("_demo"):
        return False, "first demo signal missing _demo marker"
    if first.get("ticker") != "AAPL":
        return False, f"unexpected demo ticker: {first.get('ticker')}"
    if not isinstance(diagnostics, dict):
        return False, "diagnostics payload is not a dict"
    return True, "demo scan path"


def _check_web_health() -> tuple[bool, str]:
    from fastapi.testclient import TestClient

    from webapp.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        if r.status_code != 200:
            return False, f"/api/health status {r.status_code}"
        payload = r.json()
        if not payload.get("ok"):
            return False, "/api/health returned ok=false"
        data = payload.get("data") or {}
        if data.get("status") != "ok":
            return False, f"unexpected health status: {data.get('status')}"
    return True, "web health contract"


def _check_saas_web_health() -> tuple[bool, str]:
    from fastapi.testclient import TestClient

    overrides = {
        "SAAS_HEALTH_REQUIRE_REDIS": "0",
        "SAAS_HEALTH_REQUIRE_WORKERS": "0",
    }
    prev: dict[str, str | None] = {k: os.environ.get(k) for k in overrides}
    for k, v in overrides.items():
        os.environ[k] = v
    try:
        from webapp.main_saas import app as saas_app

        with TestClient(saas_app) as client:
            health = client.get("/api/health")
            if health.status_code != 200:
                return False, f"saas /api/health status {health.status_code}"
            payload = health.json()
            if not payload.get("ok"):
                return False, "saas /api/health returned ok=false"
            ready = client.get("/api/health/ready")
            if ready.status_code != 200:
                return False, f"saas /api/health/ready status {ready.status_code}"
    finally:
        for k, original in prev.items():
            if original is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original
    return True, "saas health contract"


def _check_discord_payload_shape() -> tuple[bool, str]:
    from signal_scanner import _build_comparison_embed

    signals = [
        {"ticker": "AAPL", "signal_score": 90, "mirofish_conviction": 40, "sector_etf": "XLK"},
        {"ticker": "MSFT", "signal_score": 85, "mirofish_conviction": 35, "sector_etf": "XLK"},
        {"ticker": "NVDA", "signal_score": 80, "mirofish_conviction": 30, "sector_etf": "XLK"},
    ]
    embed = _build_comparison_embed(signals)
    if not embed:
        return False, "comparison embed was not generated"
    fields = embed.get("fields") or []
    if len(fields) != 3:
        return False, f"unexpected field count: {len(fields)}"
    title = str(embed.get("title", ""))
    if "Scan Results" not in title:
        return False, f"unexpected title: {title}"
    return True, "discord payload contract"


def main() -> int:
    checks = [
        _check_demo_scan,
        _check_web_health,
        _check_saas_web_health,
        _check_discord_payload_shape,
    ]
    failures: list[str] = []
    for fn in checks:
        ok, label = fn()
        if ok:
            print(f"PASS: {label}")
        else:
            failures.append(label)
            print(f"FAIL: {label}")

    if failures:
        print(f"Smoke checks failed: {failures}")
        return 1
    print("PASS: validation smoke checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

