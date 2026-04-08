#!/usr/bin/env python3
"""
Aggregate optimization artifacts and classify parameter outcomes.

Classification:
- Promote: robust winner across windows without risk-quality degradation
- Watch: mixed outcomes or insufficient evidence
- Keep: repeatedly rejected or no robust improvement
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
EXPECTED_TUNABLE_KEYS = [
    "QUALITY_GATES_MODE",
    "QUALITY_SOFT_MIN_REASONS",
    "QUALITY_MIN_SIGNAL_SCORE",
    "QUALITY_BREAKOUT_VOLUME_MIN_RATIO",
    "QUALITY_REQUIRE_BREAKOUT_VOLUME",
    "ADVISORY_CONFIDENCE_HIGH",
    "ADVISORY_CONFIDENCE_LOW",
    "SIGNAL_TOP_N",
    "SIGNAL_UNIVERSE_TARGET_SIZE",
]


def _drawdown_mag(metrics: dict[str, Any]) -> float:
    dd = float(
        metrics.get(
            "max_drawdown_net_pct",
            metrics.get("max_drawdown_pct", metrics.get("drawdown_worst", 0)),
        )
        or 0
    )
    return abs(min(0.0, dd))


def _pf_net(metrics: dict[str, Any]) -> float:
    return float(metrics.get("profit_factor_net", metrics.get("profit_factor", metrics.get("pf_mean", 0))) or 0)


def _trades(metrics: dict[str, Any]) -> int:
    return int(metrics.get("total_trades", metrics.get("trades_min", 0)) or 0)


def _parse_mutation_key(mutation: str) -> str | None:
    if ": " not in mutation:
        return None
    return mutation.split(": ", 1)[0].strip()


def _classify_parameter(
    key: str,
    rows: list[dict[str, Any]],
    min_required_windows: int,
) -> dict[str, Any]:
    accepted = [r for r in rows if r["accepted"]]
    robust = [r for r in accepted if r["pf_ok"] and r["dd_ok"] and r["trades_ok"]]
    windows_all = sorted(set(r["window"] for r in rows))
    windows_robust = sorted(set(r["window"] for r in robust))

    promote = (
        len(windows_robust) >= min_required_windows
        and len(robust) >= max(2, min_required_windows)
    )
    if promote:
        label = "Promote"
        why = "robust accepted improvements across windows"
    elif not accepted:
        label = "Keep"
        why = "no accepted improvements"
    elif robust:
        label = "Watch"
        why = "some robust wins but not enough cross-window evidence"
    else:
        label = "Keep"
        why = "accepted candidates failed PF/DD/trade robustness checks"

    best_obj = max((r["objective"] for r in rows), default=0.0)
    return {
        "parameter": key,
        "classification": label,
        "why": why,
        "trials": len(rows),
        "accepted_trials": len(accepted),
        "robust_accepted_trials": len(robust),
        "windows_observed": windows_all,
        "windows_with_robust_wins": windows_robust,
        "best_objective_seen": round(float(best_obj), 4),
        "sample_mutations": sorted({r["mutation"] for r in rows})[:6],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Review optimization artifacts and classify parameters")
    parser.add_argument(
        "--glob",
        default="optimization_walkforward_*.json",
        help="Artifact glob pattern inside validation_artifacts",
    )
    parser.add_argument(
        "--min-required-windows",
        type=int,
        default=2,
        help="Minimum windows with robust wins to classify as Promote",
    )
    args = parser.parse_args()

    files = sorted(ARTIFACT_DIR.glob(args.glob))
    if not files:
        print("No optimization artifacts found.")
        return 1

    rows_by_param: dict[str, list[dict[str, Any]]] = defaultdict(list)
    run_summaries: list[dict[str, Any]] = []

    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8"))
        baseline_hist = None
        for h in data.get("history", []):
            if int(h.get("round", -1)) == 0:
                baseline_hist = h
                break
        if baseline_hist is None:
            continue
        baseline_metrics = (data.get("baseline_walk_forward", {}) or {}).get("aggregates", {}) or {}
        start_date = str(data.get("run_id", "unknown"))
        gates = data.get("gates", {}) if isinstance(data, dict) else {}
        max_dd_slack = float(gates.get("max_drawdown_degrade", 2.0) or 2.0)
        min_trades = int(gates.get("min_trades", 25) or 25)
        run_summaries.append(
            {
                "file": fp.name,
                "run_id": data.get("run_id"),
                "start_date": start_date,
                "best_objective": float(data.get("best_objective", 0) or 0),
                "best_params": data.get("best_params", {}),
            }
        )

        for h in data.get("history", []):
            if int(h.get("round", 0)) == 0:
                continue
            mutation = str(h.get("mutation", ""))
            key = _parse_mutation_key(mutation)
            if not key:
                continue
            metrics = h.get("aggregates", {}) or {}
            b_pf = _pf_net(baseline_metrics)
            c_pf = _pf_net(metrics)
            b_dd = _drawdown_mag(baseline_metrics)
            c_dd = _drawdown_mag(metrics)
            b_trades = _trades(baseline_metrics)
            c_trades = _trades(metrics)
            rows_by_param[key].append(
                {
                    "artifact": fp.name,
                    "window": start_date,
                    "mutation": mutation,
                    "accepted": bool(h.get("accepted")),
                    "objective": float(h.get("objective", 0) or 0),
                    "baseline_pf_net": b_pf,
                    "candidate_pf_net": c_pf,
                    "baseline_dd_mag": b_dd,
                    "candidate_dd_mag": c_dd,
                    "baseline_trades": b_trades,
                    "candidate_trades": c_trades,
                    "pf_ok": c_pf >= max(1.0, b_pf - 0.03),
                    "dd_ok": c_dd <= (b_dd + max_dd_slack),
                    "trades_ok": c_trades >= min_trades,
                }
            )

    classifications = [
        _classify_parameter(k, v, min_required_windows=max(1, args.min_required_windows))
        for k, v in sorted(rows_by_param.items())
    ]
    seen = {c["parameter"] for c in classifications}
    for key in EXPECTED_TUNABLE_KEYS:
        if key in seen:
            continue
        classifications.append(
            {
                "parameter": key,
                "classification": "Watch",
                "why": "insufficient evidence (no mutations tested yet)",
                "trials": 0,
                "accepted_trials": 0,
                "robust_accepted_trials": 0,
                "windows_observed": [],
                "windows_with_robust_wins": [],
                "best_objective_seen": 0.0,
                "sample_mutations": [],
            }
        )
    classifications = sorted(classifications, key=lambda r: r["parameter"])

    overall_best = max(run_summaries, key=lambda r: r["best_objective"], default=None)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(files),
        "overall_best_run": overall_best,
        "classifications": classifications,
        "runs": run_summaries,
    }

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = ARTIFACT_DIR / f"best_fit_review_{run_id}.json"
    out_md = ARTIFACT_DIR / f"best_fit_review_{run_id}.md"
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Best Fit Review")
    lines.append("")
    lines.append(f"- Generated: `{result['generated_at']}`")
    lines.append(f"- Optimization artifacts reviewed: `{len(files)}`")
    if overall_best:
        lines.append(f"- Overall best run: `{overall_best['file']}`")
        lines.append(f"- Best objective: `{overall_best['best_objective']:.4f}`")
    lines.append("")
    lines.append("## Parameter Classification")
    lines.append("")
    for row in classifications:
        lines.append(
            f"- `{row['parameter']}`: **{row['classification']}** "
            f"({row['why']}; trials={row['trials']}, accepted={row['accepted_trials']}, robust={row['robust_accepted_trials']})"
        )
    lines.append("")
    lines.append("## Current Recommendation")
    lines.append("")
    promote = [r["parameter"] for r in classifications if r["classification"] == "Promote"]
    watch = [r["parameter"] for r in classifications if r["classification"] == "Watch"]
    keep = [r["parameter"] for r in classifications if r["classification"] == "Keep"]
    lines.append(f"- Promote now: `{', '.join(promote) if promote else 'none'}`")
    lines.append(f"- Watch next: `{', '.join(watch) if watch else 'none'}`")
    lines.append(f"- Keep as-is: `{', '.join(keep) if keep else 'none'}`")
    lines.append("")
    lines.append("## Promotion Rule Applied")
    lines.append("")
    lines.append(
        "- Promote only when robust accepted wins are present across windows "
        "and PF/DD/trade guardrails pass; otherwise keep or watch."
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Best-fit review JSON: {out_json}")
    print(f"Best-fit review report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
