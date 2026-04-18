"""Run two backtests over the same window — one with the new intelligence
overlays *off* (control) and one with them *on* (treatment) — and emit a
delta report.

This is the missing leg of the [[promotion-playbook]]: it converts
"shadow-only" plugin metrics into a concrete historical PnL attribution that
the operator can use as gate evidence when promoting any of:

* ``META_POLICY_MODE``
* ``UNCERTAINTY_MODE``
* ``MIROFISH_WEIGHTING_MODE``
* ``EVENT_RISK_MODE``
* ``EXIT_MANAGER_MODE``
* ``EXEC_QUALITY_MODE``

Usage::

    python scripts/backtest_intelligence_compare.py \\
        --start-date 2018-01-01 --end-date 2024-12-31 \\
        --tickers AAPL MSFT NVDA AMZN GOOG META \\
        --treatment all_live \\
        --output validation_artifacts/intel_compare.json

By default the script enables every overlay in *live* mode for the treatment
arm. Pass ``--treatment meta_policy``, ``--treatment exit_manager``, etc. to
isolate one overlay at a time, which is what you want when generating the
"effect direction" gate evidence for a single feature.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from backtest import run_backtest  # noqa: E402
from backtest_intelligence import BacktestIntelligenceConfig  # noqa: E402

LOG = logging.getLogger("backtest_intelligence_compare")

OVERLAY_PRESETS: dict[str, dict[str, str]] = {
    "all_live": {
        "meta_policy": "live",
        "event_risk": "live",
        "exit_manager": "live",
        "exec_quality": "live",
    },
    "all_shadow": {
        "meta_policy": "shadow",
        "event_risk": "shadow",
        "exit_manager": "shadow",
        "exec_quality": "shadow",
    },
    "meta_policy": {"meta_policy": "live"},
    "event_risk": {"event_risk": "live"},
    "exit_manager": {"exit_manager": "live"},
    "exec_quality": {"exec_quality": "live"},
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest intelligence overlay comparison")
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD; defaults to 10y ago")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD; defaults to today")
    p.add_argument("--tickers", nargs="*", default=None, help="Optional ticker filter")
    p.add_argument(
        "--treatment",
        choices=sorted(OVERLAY_PRESETS.keys()),
        default="all_live",
        help="Which overlay configuration to test (default: all_live)",
    )
    p.add_argument(
        "--output",
        default=str(SKILL_DIR / "validation_artifacts" / "intelligence_overlay_compare.json"),
        help="Path to write the JSON delta report",
    )
    p.add_argument("--include-trades", action="store_true", help="Embed full trade lists in the report")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _summary(label: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": label,
        "intelligence_overlay": result.get("intelligence_overlay"),
        "total_trades": result.get("total_trades"),
        "win_rate": result.get("win_rate"),
        "win_rate_net": result.get("win_rate_net"),
        "total_return_pct": result.get("total_return_pct"),
        "total_return_net_pct": result.get("total_return_net_pct"),
        "cagr_pct": result.get("cagr_pct"),
        "cagr_net_pct": result.get("cagr_net_pct"),
        "avg_return_pct": result.get("avg_return_pct"),
        "avg_return_net_pct": result.get("avg_return_net_pct"),
        "profit_factor": result.get("profit_factor"),
        "profit_factor_net": result.get("profit_factor_net"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
        "diagnostics": result.get("diagnostics"),
        "universe_size": result.get("universe_size"),
    }


def _coerce_pf(value: Any) -> float | None:
    if value in (None, "inf"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta(control: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "total_trades",
        "win_rate",
        "win_rate_net",
        "total_return_pct",
        "total_return_net_pct",
        "cagr_pct",
        "cagr_net_pct",
        "avg_return_pct",
        "avg_return_net_pct",
        "max_drawdown_pct",
        "max_drawdown_net_pct",
    )
    deltas: dict[str, Any] = {}
    for key in keys:
        c, t = control.get(key), treatment.get(key)
        if isinstance(c, (int, float)) and isinstance(t, (int, float)):
            deltas[key] = round(t - c, 4)
        else:
            deltas[key] = None
    cpf, tpf = _coerce_pf(control.get("profit_factor_net")), _coerce_pf(treatment.get("profit_factor_net"))
    deltas["profit_factor_net"] = round(tpf - cpf, 4) if (cpf is not None and tpf is not None) else None

    # Direction labels make the report skimmable.
    def _verdict(metric: str, larger_is_better: bool) -> str:
        d = deltas.get(metric)
        if d is None:
            return "n/a"
        if abs(d) < 1e-6:
            return "neutral"
        positive = d > 0
        good = positive if larger_is_better else (not positive)
        return "improved" if good else "regressed"

    deltas["_verdict"] = {
        "win_rate_net": _verdict("win_rate_net", True),
        "total_return_net_pct": _verdict("total_return_net_pct", True),
        "cagr_net_pct": _verdict("cagr_net_pct", True),
        "max_drawdown_net_pct": _verdict("max_drawdown_net_pct", True),  # less negative is better
        "profit_factor_net": _verdict("profit_factor_net", True),
    }
    return deltas


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    end = args.end_date or datetime.now().strftime("%Y-%m-%d")
    start = args.start_date or (datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d")
    treatment_overlay = OVERLAY_PRESETS[args.treatment]

    LOG.info("Backtest window: %s -> %s", start, end)
    LOG.info("Treatment preset: %s -> %s", args.treatment, treatment_overlay)

    LOG.info("Running CONTROL backtest (overlays all off)...")
    control = run_backtest(
        tickers=args.tickers,
        start_date=start,
        end_date=end,
        include_all_trades=args.include_trades,
        intelligence_overlay=BacktestIntelligenceConfig.all_off(),
    )
    LOG.info(
        "  control: %s trades, net %.2f%% return, max DD %.2f%%",
        control.get("total_trades"),
        control.get("total_return_net_pct") or 0.0,
        control.get("max_drawdown_net_pct") or 0.0,
    )

    LOG.info("Running TREATMENT backtest (%s)...", args.treatment)
    treatment = run_backtest(
        tickers=args.tickers,
        start_date=start,
        end_date=end,
        include_all_trades=args.include_trades,
        intelligence_overlay=treatment_overlay,
    )
    LOG.info(
        "  treatment: %s trades, net %.2f%% return, max DD %.2f%%",
        treatment.get("total_trades"),
        treatment.get("total_return_net_pct") or 0.0,
        treatment.get("max_drawdown_net_pct") or 0.0,
    )

    control_summary = _summary("control_off", control)
    treatment_summary = _summary(f"treatment_{args.treatment}", treatment)
    delta = _delta(control_summary, treatment_summary)

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "start_date": start,
        "end_date": end,
        "tickers_filter": args.tickers,
        "treatment_preset": args.treatment,
        "treatment_overlay": treatment_overlay,
        "control": control_summary,
        "treatment": treatment_summary,
        "delta": delta,
        "verdict": delta.get("_verdict"),
    }
    if args.include_trades:
        report["control"]["trades"] = control.get("trades", [])
        report["treatment"]["trades"] = treatment.get("trades", [])

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    LOG.info("Wrote %s", out_path)

    print("\n=== Intelligence overlay comparison ===")
    print(f"  preset:               {args.treatment}")
    print(f"  trades:               {control_summary['total_trades']} -> {treatment_summary['total_trades']}")
    print(f"  win_rate_net (%):     {control_summary['win_rate_net']} -> {treatment_summary['win_rate_net']}    (Δ {delta['win_rate_net']})")
    print(f"  total_return_net (%): {control_summary['total_return_net_pct']} -> {treatment_summary['total_return_net_pct']}    (Δ {delta['total_return_net_pct']})")
    print(f"  cagr_net (%):         {control_summary['cagr_net_pct']} -> {treatment_summary['cagr_net_pct']}    (Δ {delta['cagr_net_pct']})")
    print(f"  max_dd_net (%):       {control_summary['max_drawdown_net_pct']} -> {treatment_summary['max_drawdown_net_pct']}    (Δ {delta['max_drawdown_net_pct']})")
    print(f"  profit_factor_net:    {control_summary['profit_factor_net']} -> {treatment_summary['profit_factor_net']}    (Δ {delta['profit_factor_net']})")
    print("\n  verdicts:")
    for metric, verdict in (delta.get("_verdict") or {}).items():
        print(f"    {metric}: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
