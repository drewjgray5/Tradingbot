#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SKILL_DIR))

from config import (  # noqa: E402
    get_data_integrity_fail_on_silent_fallback,
    get_data_integrity_max_fallback_unknown_count,
    get_data_integrity_min_history_bars,
    get_data_integrity_min_history_coverage_pct,
    get_data_integrity_min_pm_coverage_pct,
)
from market_data import get_daily_history_with_meta  # noqa: E402
from prediction_market import load_historical_provider  # noqa: E402
from prediction_market_experiment import load_frozen_universe  # noqa: E402
from schwab_auth import DualSchwabAuth  # noqa: E402


def _pct(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return round((float(n) / float(d)) * 100.0, 4)


def _iso_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _render_md(report: dict[str, Any]) -> str:
    cfg = report["gate_config"]
    h = report["history_coverage"]
    pm = report["pm_coverage"]
    fb = report["fallback_accounting"]
    fails = report["gate_failures"]
    lines = [
        "# Data Integrity Gate Report",
        "",
        f"- run_at: `{report['run_at']}`",
        f"- passed: `{report['passed']}`",
        f"- start_date: `{report['start_date']}`",
        f"- end_date: `{report['end_date']}`",
        "",
        "## Gate Config",
        "",
        f"- min_history_coverage_pct: `{cfg['min_history_coverage_pct']}`",
        f"- min_history_bars: `{cfg['min_history_bars']}`",
        f"- min_pm_coverage_pct: `{cfg['min_pm_coverage_pct']}`",
        f"- fail_on_silent_fallback: `{cfg['fail_on_silent_fallback']}`",
        f"- max_fallback_unknown_count: `{cfg['max_fallback_unknown_count']}`",
        "",
        "## History Coverage",
        "",
        f"- covered_symbols: `{h['covered_symbols']}/{h['symbols_total']}`",
        f"- coverage_pct: `{h['coverage_pct']}`",
        "",
        "## PM Coverage",
        "",
        f"- matched_points: `{pm['matched_points']}/{pm['points_total']}`",
        f"- coverage_pct: `{pm['coverage_pct']}`",
        f"- exclusion_reasons: `{json.dumps(pm['exclusion_reasons'])}`",
        "",
        "## Fallback Accounting",
        "",
        f"- provider_counts: `{json.dumps(fb['provider_counts'])}`",
        f"- unknown_provider_count: `{fb['unknown_provider_count']}`",
        f"- missing_fallback_reason_count: `{fb['missing_fallback_reason_count']}`",
        f"- silent_fallback_count: `{fb['silent_fallback_count']}`",
        "",
    ]
    if fails:
        lines.extend(["## Failures", ""])
        for r in fails:
            lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def run_validation(
    *,
    start_date: str,
    end_date: str,
    universe_file: Path,
    pm_historical_file: Path,
    skill_dir: Path,
) -> dict[str, Any]:
    tickers = load_frozen_universe(universe_file, start_date=start_date)
    auth = DualSchwabAuth(skill_dir=skill_dir)
    min_history_bars = int(get_data_integrity_min_history_bars(skill_dir))
    min_history_cov_pct = float(get_data_integrity_min_history_coverage_pct(skill_dir))
    min_pm_cov_pct = float(get_data_integrity_min_pm_coverage_pct(skill_dir))
    fail_on_silent = bool(get_data_integrity_fail_on_silent_fallback(skill_dir))
    max_unknown = int(get_data_integrity_max_fallback_unknown_count(skill_dir))

    provider_counts: Counter[str] = Counter()
    fallback_reason_counts: Counter[str] = Counter()
    history_missing: list[str] = []
    history_points: dict[str, list[datetime]] = {}
    unknown_provider_count = 0
    missing_fallback_reason_count = 0

    for ticker in tickers:
        df, meta = get_daily_history_with_meta(ticker=ticker, days=5000, auth=auth, skill_dir=skill_dir)
        provider = str(meta.get("provider") or "unknown")
        provider_counts[provider] += 1
        if provider not in {"schwab", "yfinance"}:
            unknown_provider_count += 1
        if provider == "yfinance":
            reason = str(meta.get("fallback_reason") or "").strip()
            if not reason:
                missing_fallback_reason_count += 1
                fallback_reason_counts["missing"] += 1
            else:
                fallback_reason_counts[reason] += 1
        if df is None or df.empty or len(df) < min_history_bars:
            history_missing.append(ticker)
            continue
        # Constrain to requested backtest window.
        in_window = df.loc[(df.index >= start_date) & (df.index <= end_date)]
        if in_window is None or in_window.empty:
            history_missing.append(ticker)
            continue
        if len(in_window) < min_history_bars:
            history_missing.append(ticker)
            continue
        # Sample at most 40 points per symbol for PM coverage checks.
        step = max(1, int(len(in_window) / 40))
        samples = in_window.index[::step].tolist()
        points: list[datetime] = []
        for idx in samples:
            dt = idx.to_pydatetime()
            if dt.tzinfo is None:
                points.append(dt.replace(tzinfo=timezone.utc))
            else:
                points.append(dt.astimezone(timezone.utc))
        history_points[ticker] = points

    covered_symbols = len(history_points)
    symbols_total = len(tickers)
    history_cov_pct = _pct(covered_symbols, symbols_total)

    provider = load_historical_provider(pm_historical_file)
    matched_points = 0
    points_total = 0
    pm_exclusion: Counter[str] = Counter()
    for ticker, points in history_points.items():
        for as_of in points:
            points_total += 1
            snap = provider.lookup_event(ticker=ticker, as_of=as_of)
            if snap is None:
                pm_exclusion["no_match"] += 1
                continue
            if snap.updated_ts is None:
                pm_exclusion["missing_updated_ts"] += 1
                continue
            matched_points += 1
    pm_cov_pct = _pct(matched_points, points_total)
    silent_fallback_count = int(unknown_provider_count + missing_fallback_reason_count)

    gate_failures: list[str] = []
    if history_cov_pct < min_history_cov_pct:
        gate_failures.append(
            f"history_coverage_below_threshold:{history_cov_pct:.4f}<{min_history_cov_pct:.4f}"
        )
    if pm_cov_pct < min_pm_cov_pct:
        gate_failures.append(
            f"pm_coverage_below_threshold:{pm_cov_pct:.4f}<{min_pm_cov_pct:.4f}"
        )
    if unknown_provider_count > max_unknown:
        gate_failures.append(
            f"unknown_provider_count_exceeded:{unknown_provider_count}>{max_unknown}"
        )
    if fail_on_silent and silent_fallback_count > 0:
        gate_failures.append(f"silent_fallback_detected:{silent_fallback_count}")

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "passed": len(gate_failures) == 0,
        "gate_failures": gate_failures,
        "gate_config": {
            "min_history_coverage_pct": min_history_cov_pct,
            "min_history_bars": min_history_bars,
            "min_pm_coverage_pct": min_pm_cov_pct,
            "fail_on_silent_fallback": fail_on_silent,
            "max_fallback_unknown_count": max_unknown,
        },
        "history_coverage": {
            "symbols_total": symbols_total,
            "covered_symbols": covered_symbols,
            "coverage_pct": history_cov_pct,
            "missing_symbols": history_missing[:200],
        },
        "pm_coverage": {
            "points_total": points_total,
            "matched_points": matched_points,
            "coverage_pct": pm_cov_pct,
            "exclusion_reasons": dict(pm_exclusion),
        },
        "fallback_accounting": {
            "provider_counts": dict(provider_counts),
            "fallback_reason_counts": dict(fallback_reason_counts),
            "unknown_provider_count": unknown_provider_count,
            "missing_fallback_reason_count": missing_fallback_reason_count,
            "silent_fallback_count": silent_fallback_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-run data integrity validator for PM A/B pipeline")
    parser.add_argument("--start-date", required=True, help="Backtest start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Backtest end date YYYY-MM-DD")
    parser.add_argument("--universe-file", required=True, help="Frozen universe JSON path")
    parser.add_argument("--pm-historical-file", required=True, help="Historical PM snapshots JSON path")
    parser.add_argument("--output-prefix", default="data_integrity", help="Artifact filename prefix")
    args = parser.parse_args()

    universe_path = Path(args.universe_file)
    pm_hist_path = Path(args.pm_historical_file)
    if not universe_path.exists():
        raise SystemExit(f"Universe file missing: {universe_path}")
    if not pm_hist_path.exists():
        raise SystemExit(f"PM historical file missing: {pm_hist_path}")
    _ = _iso_dt(args.start_date)
    _ = _iso_dt(args.end_date)

    report = run_validation(
        start_date=args.start_date,
        end_date=args.end_date,
        universe_file=universe_path,
        pm_historical_file=pm_hist_path,
        skill_dir=SKILL_DIR,
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = ARTIFACT_DIR / f"{args.output_prefix}_{run_id}.json"
    out_md = ARTIFACT_DIR / f"{args.output_prefix}_{run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_md.write_text(_render_md(report), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"JSON artifact: {out_json}")
    print(f"Markdown artifact: {out_md}")
    return 0 if bool(report.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
