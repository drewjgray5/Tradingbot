#!/usr/bin/env python3
"""Score historical counterfactual events.

Counterfactual events are written by `agent_intelligence.log_counterfactual_event`
when the meta-policy or uncertainty layer would have suppressed (or would have
fired) a signal. This script reads `.counterfactual_log.jsonl`, looks up the
forward return for each ticker over the configured horizon, and emits a summary
artifact under `validation_artifacts/counterfactual_scoring_<ts>.json`.

Closing this loop is the prerequisite for promoting `META_POLICY_MODE` and
`UNCERTAINTY_MODE` from shadow to live.

Usage:
    python scripts/score_counterfactual_outcomes.py
    python scripts/score_counterfactual_outcomes.py --horizon-days 5 --max-rows 500

Notes:
    * Forward returns come from yfinance (best effort) when the local market
      data layer is not configured for historical bars; the script degrades
      gracefully when prices are missing.
    * Only events whose timestamp is at least ``horizon_days`` in the past are
      scored; younger events are reported as ``status="pending"``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
LOG_FILE = SKILL_DIR / ".counterfactual_log.jsonl"


def _load_events(path: Path, max_rows: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if max_rows and len(out) > max_rows:
        out = out[-max_rows:]
    return out


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _forward_return(ticker: str, anchor: datetime, horizon_days: int) -> float | None:
    """Best-effort forward return lookup using yfinance.

    Returns ``None`` when prices are unavailable so the caller can mark the
    event as ``status="missing_data"`` rather than guessing.
    """
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return None
    try:
        end = anchor + timedelta(days=horizon_days * 3)  # buffer for non-trading days
        df = yf.download(
            ticker,
            start=anchor.date().isoformat(),
            end=(end + timedelta(days=1)).date().isoformat(),
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            return None
        closes = df["Close"].dropna()
        if len(closes) < 2:
            return None
        anchor_close = float(closes.iloc[0])
        target_idx = min(horizon_days, len(closes) - 1)
        target_close = float(closes.iloc[target_idx])
        if anchor_close <= 0:
            return None
        return (target_close - anchor_close) / anchor_close
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=ARTIFACT_DIR)
    args = parser.parse_args()

    events = _load_events(LOG_FILE, args.max_rows)
    if not events:
        print(f"No counterfactual events found at {LOG_FILE}", file=sys.stderr)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = args.out_dir / "counterfactual_scoring_empty.json"
        out_path.write_text(json.dumps({"events": 0, "scored": 0}, indent=2), encoding="utf-8")
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.horizon_days)

    scored: list[dict[str, Any]] = []
    suppressed_returns: list[float] = []
    fired_returns: list[float] = []

    for ev in events:
        ts = _parse_ts(str(ev.get("ts") or ""))
        ticker = str(ev.get("ticker") or "").upper()
        reason = str(ev.get("reason") or "")
        if not ts or not ticker:
            continue
        if ts > cutoff:
            scored.append({**ev, "status": "pending"})
            continue
        ret = _forward_return(ticker, ts, args.horizon_days)
        if ret is None:
            scored.append({**ev, "status": "missing_data"})
            continue
        bucket = "suppressed" if reason.startswith(("meta_policy", "uncertainty")) else "fired"
        scored.append({**ev, "status": "scored", "forward_return": ret, "bucket": bucket})
        if bucket == "suppressed":
            suppressed_returns.append(ret)
        else:
            fired_returns.append(ret)

    def _stats(name: str, values: list[float]) -> dict[str, Any]:
        if not values:
            return {"name": name, "count": 0}
        avg = sum(values) / len(values)
        wins = sum(1 for v in values if v > 0)
        return {
            "name": name,
            "count": len(values),
            "avg_return": round(avg, 5),
            "win_rate": round(wins / len(values), 4),
            "min": round(min(values), 5),
            "max": round(max(values), 5),
        }

    summary = {
        "generated_at": now.isoformat(),
        "horizon_days": int(args.horizon_days),
        "log_file": str(LOG_FILE),
        "totals": {
            "events": len(events),
            "scored": sum(1 for e in scored if e.get("status") == "scored"),
            "pending": sum(1 for e in scored if e.get("status") == "pending"),
            "missing_data": sum(1 for e in scored if e.get("status") == "missing_data"),
        },
        "buckets": {
            "suppressed": _stats("suppressed", suppressed_returns),
            "fired": _stats("fired", fired_returns),
        },
        # If suppressed signals would, on average, have outperformed fired ones,
        # the policy is being too aggressive; the reverse means it's working.
        "policy_lift_vs_fired": (
            round(
                (sum(fired_returns) / len(fired_returns) if fired_returns else 0.0)
                - (sum(suppressed_returns) / len(suppressed_returns) if suppressed_returns else 0.0),
                5,
            )
            if (fired_returns and suppressed_returns)
            else None
        ),
        "events": scored,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out_dir / f"counterfactual_scoring_{stamp}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
