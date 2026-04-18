#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from prediction_market_experiment import load_frozen_universe  # noqa: E402


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(v: Any) -> str | None:
    if v is None:
        return None
    raw = str(v).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        return raw
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _match_confidence(ticker: str, question: str, description: str) -> float:
    text = f"{question} {description}".lower()
    t = ticker.lower()
    score = 0.2
    if t in text:
        score += 0.45
    if any(k in text for k in ("earnings", "revenue", "eps", "guidance")):
        score += 0.15
    if any(k in text for k in ("week", "month", "quarter", "q1", "q2", "q3", "q4")):
        score += 0.1
    if any(k in text for k in ("stock", "shares", "price")):
        score += 0.1
    return max(0.0, min(1.0, score))


def _fetch_polymarket_for_ticker(ticker: str, limit: int) -> list[dict[str, Any]]:
    query = urllib.parse.quote_plus(ticker)
    url = (
        "https://gamma-api.polymarket.com/markets"
        f"?active=true&closed=false&limit={int(limit)}&search={query}"
    )
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build normalized PM PIT snapshot store from provider data")
    parser.add_argument("--universe-file", required=True, help="Frozen universe file path")
    parser.add_argument("--start-date", required=True, help="Start date used for universe validation")
    parser.add_argument(
        "--out-dir",
        default=str(SKILL_DIR / "experiments"),
        help="Output directory for snapshot store files",
    )
    parser.add_argument("--provider-limit", type=int, default=25, help="Max provider rows per ticker")
    args = parser.parse_args()

    universe_file = Path(args.universe_file)
    if not universe_file.exists():
        raise SystemExit(f"Universe file missing: {universe_file}")
    tickers = load_frozen_universe(universe_file, start_date=args.start_date)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    rows: list[dict[str, Any]] = []
    diag = {
        "tickers_total": len(tickers),
        "tickers_with_rows": 0,
        "tickers_without_rows": 0,
        "provider_errors": 0,
    }
    for ticker in tickers:
        try:
            provider_rows = _fetch_polymarket_for_ticker(ticker, max(1, int(args.provider_limit)))
        except Exception:
            diag["provider_errors"] = int(diag.get("provider_errors", 0) or 0) + 1
            diag["tickers_without_rows"] = int(diag.get("tickers_without_rows", 0) or 0) + 1
            continue
        if not provider_rows:
            diag["tickers_without_rows"] = int(diag.get("tickers_without_rows", 0) or 0) + 1
            continue
        diag["tickers_with_rows"] = int(diag.get("tickers_with_rows", 0) or 0) + 1
        for row in provider_rows:
            question = str(row.get("question") or row.get("title") or "").strip()
            event_id = str(row.get("id") or row.get("market_id") or "").strip()
            if not question or not event_id:
                continue
            prices = row.get("outcomePrices")
            implied = None
            if isinstance(prices, list) and prices:
                implied = _safe_float(prices[0])
            if implied is None:
                implied = _safe_float(row.get("probability"))
                if implied is not None and implied > 1.0:
                    implied = implied / 100.0
            if implied is None:
                continue
            implied = max(0.0, min(1.0, float(implied)))
            updated_ts = _ts(row.get("updatedAt") or row.get("lastTradeTime")) or now_iso
            rows.append(
                {
                    "snapshot_ts": updated_ts,
                    "ticker": ticker,
                    "event_id": event_id,
                    "event_name": question,
                    "implied_probability": implied,
                    "liquidity": _safe_float(row.get("liquidity") or row.get("liquidityNum")),
                    "spread": _safe_float(row.get("spread")),
                    "volume": _safe_float(row.get("volume24hr") or row.get("volume") or row.get("volumeNum")),
                    "resolution_ts": _ts(row.get("endDate") or row.get("closeTime")),
                    "updated_ts": updated_ts,
                    "provider": "polymarket",
                    "match_confidence": _match_confidence(
                        ticker=ticker,
                        question=question,
                        description=str(row.get("description") or ""),
                    ),
                }
            )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"pm_historical_snapshots_{run_id}.json"
    out_diag = out_dir / f"pm_historical_snapshots_{run_id}_meta.json"
    out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    out_diag.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "created_at": now_iso,
                "rows": len(rows),
                "diagnostics": diag,
                "universe_file": str(universe_file),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Snapshot store: {out_json}")
    print(f"Metadata: {out_diag}")
    print(json.dumps({"rows": len(rows), "diagnostics": diag}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
