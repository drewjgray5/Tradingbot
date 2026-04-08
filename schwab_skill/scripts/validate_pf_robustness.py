#!/usr/bin/env python3
"""
PF robustness + promotion-gate validation for champion/challenger.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _ticker_pool() -> list[str]:
    return [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "UNH",
        "HD", "PG", "MA", "DIS", "BAC", "XOM", "CVX", "KO", "PEP", "WMT",
        "IBM", "ORCL", "CRM", "ADBE", "NFLX", "INTC", "AMD", "QCOM", "TXN", "AVGO",
        "CSCO", "ACN", "NOW", "INTU", "AMAT", "LRCX", "KLAC", "MU", "SBUX", "NKE",
    ]


def _run_window(name: str, tickers: list[str], start_date: str) -> dict[str, Any]:
    from backtest import run_backtest

    result = run_backtest(
        tickers=tickers,
        start_date=start_date,
        slippage_bps_per_side=15.0,
        fee_per_share=0.005,
        min_fee_per_order=1.0,
        max_adv_participation=0.02,
    )
    return {
        "name": name,
        "start_date": start_date,
        "ticker_count": len(tickers),
        "total_trades": int(result.get("total_trades", 0) or 0),
        "win_rate_net": float(result.get("win_rate_net", result.get("win_rate", 0)) or 0),
        "profit_factor_net": float(result.get("profit_factor_net", result.get("profit_factor", 0)) or 0),
        "expectancy_net_pct": float(result.get("avg_return_net_pct", result.get("avg_return_pct", 0)) or 0),
        "max_drawdown_net_pct": float(result.get("max_drawdown_net_pct", result.get("max_drawdown_pct", 0)) or 0),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pf_vals = [float(r["profit_factor_net"]) for r in rows]
    wr_vals = [float(r["win_rate_net"]) for r in rows]
    dd_vals = [float(r["max_drawdown_net_pct"]) for r in rows]
    ex_vals = [float(r["expectancy_net_pct"]) for r in rows]
    trade_vals = [int(r["total_trades"]) for r in rows]
    oos = rows[-1] if rows else {}
    return {
        "pf_net_mean": round(sum(pf_vals) / len(pf_vals), 4),
        "pf_net_min": round(min(pf_vals), 4),
        "expectancy_net_mean": round(sum(ex_vals) / len(ex_vals), 4),
        "win_rate_net_mean": round(sum(wr_vals) / len(wr_vals), 4),
        "max_drawdown_net_worst": round(min(dd_vals), 4),
        "trades_min": int(min(trade_vals)),
        "oos_pf_net": round(float(oos.get("profit_factor_net", 0) or 0), 4),
        "oos_expectancy_net_pct": round(float(oos.get("expectancy_net_pct", 0) or 0), 4),
    }


def _run_profile(name: str, params: dict[str, str] | None = None, fast_smoke: bool = False) -> dict[str, Any]:
    params = params or {}
    if fast_smoke:
        base = 1.16 if name == "champion" else 1.20
        rows = [
            {"name": "long_20", "start_date": "2018-01-01", "ticker_count": 20, "total_trades": 90, "win_rate_net": 51.0, "profit_factor_net": base, "expectancy_net_pct": 0.22, "max_drawdown_net_pct": -18.0},
            {"name": "mid_20", "start_date": "2020-01-01", "ticker_count": 20, "total_trades": 88, "win_rate_net": 50.0, "profit_factor_net": base - 0.01, "expectancy_net_pct": 0.20, "max_drawdown_net_pct": -19.0},
            {"name": "recent_20", "start_date": "2022-01-01", "ticker_count": 20, "total_trades": 75, "win_rate_net": 49.0, "profit_factor_net": base - 0.02, "expectancy_net_pct": 0.17, "max_drawdown_net_pct": -20.0},
        ]
        return {"name": name, "windows": rows, "aggregates": _aggregate(rows)}
    pool = _ticker_pool()
    windows = [
        ("long_20", pool[:20], "2018-01-01"),
        ("mid_20", pool[:20], "2020-01-01"),
        ("recent_20", pool[:20], "2022-01-01"),
    ]
    rows: list[dict[str, Any]] = []
    old: dict[str, str | None] = {}
    try:
        for k, v in params.items():
            old[k] = os.environ.get(k)
            os.environ[k] = str(v)
        for win_name, tickers, start in windows:
            rows.append(_run_window(name=win_name, tickers=tickers, start_date=start))
    finally:
        for k, prev in old.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev
    return {"name": name, "windows": rows, "aggregates": _aggregate(rows)}


def _load_params(path_like: str) -> dict[str, str]:
    p = Path(path_like)
    if not p.is_absolute():
        p = SKILL_DIR / p
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("best_params"), dict):
        return {str(k): str(v) for k, v in data["best_params"].items()}
    if isinstance(data.get("params"), dict):
        return {str(k): str(v) for k, v in data["params"].items()}
    return {}


def _write_markdown(path: Path, champion: dict[str, Any], challenger: dict[str, Any], comparison: dict[str, Any], gates: dict[str, Any], reasons: list[str], passed: bool) -> None:
    ca = champion["aggregates"]
    na = challenger["aggregates"]
    lines = [
        "# Strategy Promotion Report",
        "",
        f"- decision: {'PASS' if passed else 'FAIL'}",
        f"- reasons: {', '.join(reasons) if reasons else 'none'}",
        "",
        "| Metric | Champion | Challenger | Delta |",
        "|---|---:|---:|---:|",
        f"| PF mean | {ca['pf_net_mean']:.4f} | {na['pf_net_mean']:.4f} | {comparison['pf_delta']:+.4f} |",
        f"| Expectancy mean (%) | {ca['expectancy_net_mean']:.4f} | {na['expectancy_net_mean']:.4f} | {comparison['expectancy_delta']:+.4f} |",
        f"| Worst drawdown (%) | {ca['max_drawdown_net_worst']:.4f} | {na['max_drawdown_net_worst']:.4f} | {comparison['drawdown_delta']:+.4f} |",
        f"| OOS PF | {ca['oos_pf_net']:.4f} | {na['oos_pf_net']:.4f} | {comparison['oos_pf_delta']:+.4f} |",
        f"| Trades min | {int(ca['trades_min'])} | {int(na['trades_min'])} | {comparison['trades_min_delta']:+d} |",
        "",
        "## Promotion Gates",
        "",
        "```json",
        json.dumps(gates, indent=2),
        "```",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="PF robustness and strategy promotion gate checks")
    parser.add_argument("--champion-artifact", default="", help="Champion params artifact (json)")
    parser.add_argument("--challenger-artifact", default="", help="Challenger params artifact (json)")
    parser.add_argument("--min-pf-delta", type=float, default=0.02)
    parser.add_argument("--min-expectancy-delta", type=float, default=0.0)
    parser.add_argument("--min-oos-pf", type=float, default=1.15)
    parser.add_argument("--min-oos-pf-delta", type=float, default=0.01)
    parser.add_argument("--max-drawdown-degrade-cap", type=float, default=2.0)
    parser.add_argument("--min-trades-threshold", type=int, default=25)
    parser.add_argument("--fast-smoke", action="store_true", help="Use deterministic synthetic windows for quick validation.")
    args = parser.parse_args()

    os.environ.setdefault("BACKTEST_SKIP_MIROFISH", "true")
    sys.path.insert(0, str(SKILL_DIR))

    champion_params = _load_params(args.champion_artifact) if args.champion_artifact else {}
    challenger_params = _load_params(args.challenger_artifact) if args.challenger_artifact else {}
    champion = _run_profile("champion", champion_params, fast_smoke=args.fast_smoke)
    challenger = _run_profile("challenger", challenger_params if challenger_params else champion_params, fast_smoke=args.fast_smoke)

    ca = champion["aggregates"]
    na = challenger["aggregates"]
    comparison = {
        "pf_delta": round(float(na["pf_net_mean"]) - float(ca["pf_net_mean"]), 6),
        "expectancy_delta": round(float(na["expectancy_net_mean"]) - float(ca["expectancy_net_mean"]), 6),
        "oos_pf_delta": round(float(na["oos_pf_net"]) - float(ca["oos_pf_net"]), 6),
        "drawdown_delta": round(float(na["max_drawdown_net_worst"]) - float(ca["max_drawdown_net_worst"]), 6),
        "trades_min_delta": int(na["trades_min"]) - int(ca["trades_min"]),
    }
    gates = {
        "min_pf_delta": float(args.min_pf_delta),
        "min_expectancy_delta": float(args.min_expectancy_delta),
        "min_oos_pf": float(args.min_oos_pf),
        "min_oos_pf_delta": float(args.min_oos_pf_delta),
        "max_drawdown_degrade_cap": float(args.max_drawdown_degrade_cap),
        "min_trades_threshold": int(args.min_trades_threshold),
    }
    reasons: list[str] = []
    passed = True
    if comparison["pf_delta"] < float(args.min_pf_delta):
        passed = False
        reasons.append(f"pf_delta_too_small:{comparison['pf_delta']:.6f}<{float(args.min_pf_delta):.6f}")
    if comparison["expectancy_delta"] < float(args.min_expectancy_delta):
        passed = False
        reasons.append(
            f"expectancy_delta_too_small:{comparison['expectancy_delta']:.6f}<{float(args.min_expectancy_delta):.6f}"
        )
    if float(na["oos_pf_net"]) < float(args.min_oos_pf):
        passed = False
        reasons.append(f"oos_pf_below_floor:{float(na['oos_pf_net']):.6f}<{float(args.min_oos_pf):.6f}")
    if comparison["oos_pf_delta"] < float(args.min_oos_pf_delta):
        passed = False
        reasons.append(f"oos_pf_delta_too_small:{comparison['oos_pf_delta']:.6f}<{float(args.min_oos_pf_delta):.6f}")
    champ_dd = abs(min(0.0, float(ca["max_drawdown_net_worst"])))
    chall_dd = abs(min(0.0, float(na["max_drawdown_net_worst"])))
    if chall_dd > champ_dd + float(args.max_drawdown_degrade_cap):
        passed = False
        reasons.append(
            f"drawdown_degraded_too_much:{chall_dd:.4f}>{champ_dd + float(args.max_drawdown_degrade_cap):.4f}"
        )
    if int(na["trades_min"]) < int(args.min_trades_threshold):
        passed = False
        reasons.append(f"trades_min_too_low:{int(na['trades_min'])}<{int(args.min_trades_threshold)}")
    if not reasons:
        reasons.append("challenger_meets_walkforward_promotion_gates")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = ARTIFACT_DIR / f"strategy_promotion_report_{run_id}.json"
    out_md = ARTIFACT_DIR / f"strategy_promotion_report_{run_id}.md"
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "reasons": reasons,
        "gates": gates,
        "comparison": comparison,
        "champion": champion,
        "challenger": challenger,
        "input_artifacts": {
            "champion_artifact": args.champion_artifact or None,
            "challenger_artifact": args.challenger_artifact or None,
        },
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_markdown(out_md, champion, challenger, comparison, gates, reasons, passed)

    if passed:
        print("PASS: strategy promotion gates satisfied")
    else:
        print("FAIL: strategy promotion gates failed")
    for reason in reasons:
        print(f"  - {reason}")
    print(f"JSON artifact: {out_json}")
    print(f"Markdown report: {out_md}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
