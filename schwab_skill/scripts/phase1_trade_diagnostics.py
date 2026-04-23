"""
Phase 1 — free trade diagnostics on existing chunk artifacts.

This script does NOT run any backtests. It re-analyses the per-chunk trade
records that ``run_multi_era_backtest_schwab_only.py`` already wrote, plus
SPY price history, to produce three pieces of evidence we need before
spending more compute:

  Q2 (counterfactual regime suppression):
      For each trade, look up SPY's status on the entry_date — close vs
      50/150/200 SMAs, and 200 SMA slope direction. Compute per-era PF
      under several "what if we'd dropped trades when SPY was X" filters.

  Q3 (stop_pct + hold_duration decomposition):
      Bucket trades by entry stop_pct decile and by hold_duration bucket;
      report PF, win rate, expectancy and trade count per bucket. Tells
      us whether the edge lives in tight-stop / short-hold trades or the
      opposite.

  Equity curve sanity:
      Reconstruct each era's equity curve trade-by-trade using the same
      ordering used by the backtest (entry_date, exit_date) so we can
      see *when* the late_bull -17% drawdown actually happened.

Inputs:
  --run-id        config_id whose chunks we read (default: control_legacy).

Outputs:
  validation_artifacts/phase1_diagnostics_<run_id>.json
  validation_artifacts/phase1_diagnostics_<run_id>.md
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
CHUNKS_DIR = ARTIFACT_DIR / "multi_era_chunks"
ERA_BOUNDS = {
    "late_bull": ("2015-01-01", "2017-12-31"),
    "volatility_chop": ("2018-01-01", "2019-12-31"),
    "crash_recovery": ("2020-01-01", "2021-12-31"),
    "bear_rates": ("2022-01-01", "2023-12-31"),
    "recent_current": ("2024-01-01", None),
}


@dataclass
class Trade:
    era: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret: float
    net_ret: float
    stop_pct: float
    signal_score: float | None = None
    exit_reason: str = ""

    @property
    def hold_days(self) -> int:
        try:
            return max(int((self.exit_date - self.entry_date).days), 0)
        except Exception:
            return 0


def _load_trades(run_id: str) -> list[Trade]:
    """Read every chunk_*.json file under multi_era_chunks/<run_id>/."""
    trades: list[Trade] = []
    base = CHUNKS_DIR / run_id
    if not base.exists():
        # Fall back to top-level "control_legacy" directory layout used by
        # the multi-era runner before per-run-id directories existed.
        for era in ERA_BOUNDS:
            era_dir = base / era
            if era_dir.exists():
                continue
            legacy_dir = CHUNKS_DIR / era
            if legacy_dir.exists():
                base = CHUNKS_DIR  # use top-level
                break
    for era in ERA_BOUNDS:
        era_dir = base / era if base != CHUNKS_DIR else CHUNKS_DIR / era
        if not era_dir.exists():
            continue
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[diag] WARN failed to read {chunk_path}: {exc}")
                continue
            for raw in payload.get("trades", []):
                try:
                    entry = pd.Timestamp(raw.get("entry_date") or "")
                    exit_ = pd.Timestamp(raw.get("exit_date") or "")
                except Exception:
                    continue
                if pd.isna(entry) or pd.isna(exit_):
                    continue
                trades.append(
                    Trade(
                        era=era,
                        entry_date=entry.normalize(),
                        exit_date=exit_.normalize(),
                        ret=float(raw.get("return", 0.0) or 0.0),
                        net_ret=float(raw.get("net_return", 0.0) or 0.0),
                        stop_pct=float(raw.get("stop_pct", 0.0) or 0.0),
                        signal_score=raw.get("signal_score"),
                        exit_reason=str(raw.get("exit_reason") or ""),
                    )
                )
    return trades


def _profit_factor(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    wins = sum(t.net_ret for t in trades if t.net_ret > 0)
    losses = -sum(t.net_ret for t in trades if t.net_ret < 0)
    if losses <= 0:
        return float("inf") if wins > 0 else None
    return wins / losses


def _win_rate(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.net_ret > 0)
    return wins / len(trades)


def _expectancy(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    return statistics.fmean(t.net_ret for t in trades)


def _bucket_summary(trades: list[Trade]) -> dict[str, Any]:
    return {
        "n": len(trades),
        "pf": _profit_factor(trades),
        "win_rate": _win_rate(trades),
        "expectancy": _expectancy(trades),
        "avg_ret": _expectancy([t for t in trades]) if trades else None,
    }


def _decile_breakdown(trades: list[Trade], key: str, n_buckets: int = 5) -> list[dict[str, Any]]:
    """Bucket trades by ``key`` into roughly equal-count quintiles."""
    if not trades:
        return []
    keyed = [(getattr(t, key), t) for t in trades if getattr(t, key) is not None]
    keyed.sort(key=lambda kv: kv[0])
    total = len(keyed)
    if total == 0:
        return []
    out: list[dict[str, Any]] = []
    bucket_size = max(total // n_buckets, 1)
    for i in range(n_buckets):
        lo = i * bucket_size
        hi = total if i == n_buckets - 1 else lo + bucket_size
        slice_ = keyed[lo:hi]
        if not slice_:
            continue
        slice_trades = [t for _, t in slice_]
        out.append({
            "bucket": i + 1,
            "key_min": slice_[0][0],
            "key_max": slice_[-1][0],
            **_bucket_summary(slice_trades),
        })
    return out


def _hold_buckets(trades: list[Trade]) -> list[dict[str, Any]]:
    bins = [(0, 5), (6, 10), (11, 20), (21, 40), (41, 9999)]
    out: list[dict[str, Any]] = []
    for lo, hi in bins:
        bucket = [t for t in trades if lo <= t.hold_days <= hi]
        out.append({
            "bucket": f"{lo}-{hi}d",
            **_bucket_summary(bucket),
        })
    return out


def _equity_curve(trades: list[Trade], starting_equity: float = 100_000.0,
                  position_pct: float = 0.10) -> list[dict[str, Any]]:
    """Replay trades in entry-date order, sizing each at ``position_pct`` of equity."""
    sorted_t = sorted(trades, key=lambda t: (t.entry_date, t.exit_date))
    eq = starting_equity
    curve: list[dict[str, Any]] = []
    peak = eq
    max_dd = 0.0
    for t in sorted_t:
        size = eq * position_pct
        eq += size * t.net_ret
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        curve.append({
            "exit_date": t.exit_date.isoformat()[:10],
            "equity": round(eq, 2),
            "drawdown_pct": round(dd * 100, 3),
        })
    return curve


def _load_spy(start: str, end: str | None) -> pd.DataFrame:
    """Fetch SPY history with 50/150/200 SMAs."""
    from backtest import _fetch_history  # type: ignore
    end_str = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    df = _fetch_history("SPY", start, end_str)
    if df.empty:
        return df
    out = df.copy()
    out["sma_50"] = out["close"].rolling(50, min_periods=50).mean()
    out["sma_150"] = out["close"].rolling(150, min_periods=150).mean()
    out["sma_200"] = out["close"].rolling(200, min_periods=200).mean()
    out["sma_200_slope_20"] = out["sma_200"].diff(20)
    return out


def _spy_state_for_date(spy: pd.DataFrame, dt: pd.Timestamp) -> dict[str, Any]:
    if spy.empty:
        return {}
    try:
        idx = spy.index.get_indexer([dt.normalize()], method="pad")[0]
    except Exception:
        return {}
    if idx < 0:
        return {}
    row = spy.iloc[idx]
    close = float(row.get("close", 0.0) or 0.0)
    s50 = float(row.get("sma_50", 0.0) or 0.0)
    s150 = float(row.get("sma_150", 0.0) or 0.0)
    s200 = float(row.get("sma_200", 0.0) or 0.0)
    slope = row.get("sma_200_slope_20", 0.0)
    try:
        slope_f = float(slope) if not pd.isna(slope) else 0.0
    except Exception:
        slope_f = 0.0
    return {
        "above_50sma": bool(close > s50) if s50 > 0 else None,
        "above_150sma": bool(close > s150) if s150 > 0 else None,
        "above_200sma": bool(close > s200) if s200 > 0 else None,
        "sma200_rising_20d": bool(slope_f > 0),
        "close": close,
        "sma_200": s200,
    }


def _counterfactual_regime(trades: list[Trade]) -> dict[str, Any]:
    """For each era, fetch SPY history once and compute counterfactual PFs."""
    out: dict[str, Any] = {}
    for era, (start, end) in ERA_BOUNDS.items():
        era_trades = [t for t in trades if t.era == era]
        if not era_trades:
            continue
        # Fetch SPY with ~250 day prefix for SMA warmup.
        warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
        spy = _load_spy(warmup_start, end)
        if spy.empty:
            out[era] = {"error": "no SPY data"}
            continue
        states: list[dict[str, Any]] = [_spy_state_for_date(spy, t.entry_date) for t in era_trades]
        # Define filter cohorts.
        cohorts: dict[str, list[Trade]] = {
            "all": era_trades,
            "spy_above_50sma": [t for t, s in zip(era_trades, states) if s.get("above_50sma")],
            "spy_above_150sma": [t for t, s in zip(era_trades, states) if s.get("above_150sma")],
            "spy_sma200_rising_20d": [t for t, s in zip(era_trades, states) if s.get("sma200_rising_20d")],
            "spy_above_50sma_AND_rising": [
                t for t, s in zip(era_trades, states)
                if s.get("above_50sma") and s.get("sma200_rising_20d")
            ],
        }
        out[era] = {
            cohort: {
                "n": len(ts),
                "pf": _profit_factor(ts),
                "win_rate": _win_rate(ts),
                "expectancy": _expectancy(ts),
                "kept_pct": round(100 * len(ts) / max(len(era_trades), 1), 2),
            }
            for cohort, ts in cohorts.items()
        }
    return out


def _format_pf(v: float | None) -> str:
    if v is None:
        return "n/a"
    if math.isinf(v):
        return "inf"
    return f"{v:.3f}"


def _format_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.2f}%"


def _format_signed_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.3f}%"


def _markdown_report(run_id: str, trades: list[Trade], analysis: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Phase 1 trade diagnostics — `{run_id}`")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append(f"Total trades analysed: **{len(trades)}**")
    lines.append("")

    # Per-era headline
    lines.append("## Per-era summary")
    lines.append("")
    lines.append("| Era | Trades | PF | Win | Expectancy | Avg hold | Median stop% |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for era in ERA_BOUNDS:
        et = [t for t in trades if t.era == era]
        if not et:
            continue
        avg_hold = statistics.fmean(t.hold_days for t in et) if et else 0
        med_stop = statistics.median(t.stop_pct for t in et) if et else 0
        lines.append(
            f"| {era} | {len(et)} | {_format_pf(_profit_factor(et))} "
            f"| {_format_pct(_win_rate(et))} | {_format_signed_pct(_expectancy(et))} "
            f"| {avg_hold:.1f}d | {med_stop * 100:.2f}% |"
        )
    lines.append("")

    # Q3a — stop_pct decomposition
    lines.append("## Q3a — Stop-pct quintile decomposition (per era)")
    lines.append("")
    for era in ERA_BOUNDS:
        et = [t for t in trades if t.era == era]
        if not et:
            continue
        deciles = _decile_breakdown(et, "stop_pct", n_buckets=5)
        lines.append(f"### {era} ({len(et)} trades)")
        lines.append("")
        lines.append("| Quintile | Stop range | N | PF | Win | Expectancy |")
        lines.append("|---:|---|---:|---:|---:|---:|")
        for d in deciles:
            lines.append(
                f"| Q{d['bucket']} | {d['key_min'] * 100:.2f}–{d['key_max'] * 100:.2f}% | {d['n']} "
                f"| {_format_pf(d['pf'])} | {_format_pct(d['win_rate'])} "
                f"| {_format_signed_pct(d['expectancy'])} |"
            )
        lines.append("")

    # Q3b — hold-duration buckets
    lines.append("## Q3b — Hold-duration bucket decomposition (per era)")
    lines.append("")
    for era in ERA_BOUNDS:
        et = [t for t in trades if t.era == era]
        if not et:
            continue
        buckets = _hold_buckets(et)
        lines.append(f"### {era} ({len(et)} trades)")
        lines.append("")
        lines.append("| Bucket | N | PF | Win | Expectancy |")
        lines.append("|---|---:|---:|---:|---:|")
        for b in buckets:
            lines.append(
                f"| {b['bucket']} | {b['n']} | {_format_pf(b['pf'])} "
                f"| {_format_pct(b['win_rate'])} | {_format_signed_pct(b['expectancy'])} |"
            )
        lines.append("")

    # Q2 — counterfactual regime suppression
    cf = analysis.get("counterfactual_regime", {})
    if cf:
        lines.append("## Q2 — Counterfactual regime suppression (per era)")
        lines.append("")
        lines.append(
            "How would PF change if we had kept only trades whose entry_date "
            "fell in a stricter SPY regime? `kept_pct` = % of original trades."
        )
        lines.append("")
        cohorts = ["all", "spy_above_50sma", "spy_above_150sma",
                   "spy_sma200_rising_20d", "spy_above_50sma_AND_rising"]
        for era, era_data in cf.items():
            if "error" in era_data:
                lines.append(f"### {era}: {era_data['error']}")
                continue
            lines.append(f"### {era}")
            lines.append("")
            lines.append("| Cohort | N | Kept% | PF | Win | Expectancy |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for c in cohorts:
                d = era_data.get(c, {})
                lines.append(
                    f"| {c} | {d.get('n', 0)} | {d.get('kept_pct', 0):.1f}% "
                    f"| {_format_pf(d.get('pf'))} | {_format_pct(d.get('win_rate'))} "
                    f"| {_format_signed_pct(d.get('expectancy'))} |"
                )
            lines.append("")

    # Equity curve drawdown breakdown
    lines.append("## Equity curve: peak DD per era (10% sizing replay)")
    lines.append("")
    lines.append("| Era | Final equity | Peak DD | Trades |")
    lines.append("|---|---:|---:|---:|")
    for era in ERA_BOUNDS:
        et = [t for t in trades if t.era == era]
        if not et:
            continue
        curve = _equity_curve(et)
        if not curve:
            continue
        final = curve[-1]["equity"]
        max_dd = max(point["drawdown_pct"] for point in curve)
        lines.append(f"| {era} | ${final:,.0f} | {max_dd:.2f}% | {len(et)} |")
    lines.append("")

    # Synthesis prompt for the analyst
    lines.append("## Reading the deltas")
    lines.append("")
    lines.append(
        "* If **stop-pct quintile** PFs are roughly flat → tighter/looser stops "
        "won't fix the strategy on their own.\n"
        "* If **short-hold buckets** dominate → the edge decays after entry; "
        "exit_manager max-hold is the right lever.\n"
        "* If **`spy_above_50sma_AND_rising` PF** materially exceeds `all` PF "
        "without dropping below the 50-trade floor → regime suppression alone "
        "moves the needle and Phase 2 is worth standing up.\n"
        "* If equity curves show drawdowns concentrated in well-defined regimes "
        "(e.g. 2015 H2, 2018 Q4) → time-localised damage that suppression can fix."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 trade diagnostics on existing chunks.")
    parser.add_argument("--run-id", default="control_legacy",
                        help="Sub-directory of multi_era_chunks/ to analyse.")
    parser.add_argument("--no-spy", action="store_true",
                        help="Skip the SPY-fetch counterfactual regime analysis (offline mode).")
    args = parser.parse_args()

    print(f"[diag] loading trades for run_id={args.run_id} ...")
    trades = _load_trades(args.run_id)
    print(f"[diag] loaded {len(trades)} trades across {len({t.era for t in trades})} eras")

    analysis: dict[str, Any] = {
        "stop_pct_quintiles": {
            era: _decile_breakdown([t for t in trades if t.era == era], "stop_pct", 5)
            for era in ERA_BOUNDS
        },
        "hold_buckets": {
            era: _hold_buckets([t for t in trades if t.era == era])
            for era in ERA_BOUNDS
        },
    }
    if not args.no_spy:
        print("[diag] running counterfactual regime suppression (fetches SPY) ...")
        try:
            analysis["counterfactual_regime"] = _counterfactual_regime(trades)
        except Exception as exc:
            print(f"[diag] counterfactual regime failed: {exc}")
            analysis["counterfactual_regime"] = {"error": str(exc)}

    out_json = ARTIFACT_DIR / f"phase1_diagnostics_{args.run_id}.json"
    out_md = ARTIFACT_DIR / f"phase1_diagnostics_{args.run_id}.md"
    out_json.write_text(
        json.dumps({"run_id": args.run_id, "trade_count": len(trades), "analysis": analysis},
                   indent=2, default=str),
        encoding="utf-8",
    )
    out_md.write_text(_markdown_report(args.run_id, trades, analysis), encoding="utf-8")
    print(f"[diag] wrote {out_json}")
    print(f"[diag] wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
