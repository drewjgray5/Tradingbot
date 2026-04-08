#!/usr/bin/env python3
"""
Rank walk-forward optimization artifacts by OOS robustness.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _score(aggregates: dict[str, Any]) -> float:
    oos_pf = float(aggregates.get("oos_pf", 0) or 0)
    oos_expectancy = float(aggregates.get("oos_expectancy", 0) or 0)
    trades = int(aggregates.get("oos_trades", 0) or 0)
    dd = abs(min(0.0, float(aggregates.get("oos_drawdown", 0) or 0)))
    pf_mean = float(aggregates.get("pf_mean", 0) or 0)
    return (130.0 * oos_pf) + (22.0 * oos_expectancy) + (0.15 * trades) + (30.0 * pf_mean) - (2.0 * dd)


def _artifact_to_candidate(fp: Path, min_oos_pf: float, min_trades: int) -> dict[str, Any] | None:
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    best = (data.get("best_walk_forward") or {}).get("aggregates") or {}
    if not best:
        return None
    oos_pf = float(best.get("oos_pf", 0) or 0)
    oos_trades = int(best.get("oos_trades", 0) or 0)
    passed = oos_pf >= float(min_oos_pf) and oos_trades >= int(min_trades)
    return {
        "artifact": str(fp),
        "file": fp.name,
        "run_id": str(data.get("run_id") or fp.stem),
        "best_params": data.get("best_params", {}),
        "aggregates": best,
        "passed_robust_gate": passed,
        "robust_score": round(_score(best), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank walk-forward candidates by OOS robustness")
    parser.add_argument("--glob", default="optimization_walkforward_*.json")
    parser.add_argument("--min-oos-pf", type=float, default=1.15)
    parser.add_argument("--min-trades", type=int, default=35)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    files = sorted(ARTIFACT_DIR.glob(args.glob))
    if not files:
        print("No optimization artifacts found.")
        return 1

    candidates: list[dict[str, Any]] = []
    for fp in files:
        row = _artifact_to_candidate(fp, min_oos_pf=args.min_oos_pf, min_trades=args.min_trades)
        if row is None:
            continue
        candidates.append(row)
    if not candidates:
        print("No valid optimization candidates found.")
        return 1

    candidates.sort(key=lambda c: (bool(c["passed_robust_gate"]), float(c["robust_score"])), reverse=True)
    top = candidates[: max(1, int(args.top_k))]
    selected = top[0]
    selected["selection_reason"] = "highest robust_score among robust-gate-pass candidates"

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = ARTIFACT_DIR / f"optimization_candidate_ranking_{run_id}.json"
    out_md = ARTIFACT_DIR / f"optimization_candidate_ranking_{run_id}.md"
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gates": {"min_oos_pf": float(args.min_oos_pf), "min_trades": int(args.min_trades)},
        "total_candidates": len(candidates),
        "selected": selected,
        "top_candidates": top,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Optimization Candidate Ranking",
        "",
        f"- selected: `{selected['file']}`",
        f"- selected_oos_pf: `{float(selected['aggregates'].get('oos_pf', 0)):.4f}`",
        f"- selected_oos_trades: `{int(selected['aggregates'].get('oos_trades', 0))}`",
        f"- selected_robust_score: `{float(selected['robust_score']):.4f}`",
        "",
        "## Top Candidates",
        "",
        "| File | OOS PF | OOS Trades | OOS Drawdown (%) | Score | Robust Gate |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in top:
        agg = row["aggregates"]
        lines.append(
            f"| {row['file']} | {float(agg.get('oos_pf', 0)):.4f} | {int(agg.get('oos_trades', 0))} | "
            f"{float(agg.get('oos_drawdown', 0)):.4f} | {float(row['robust_score']):.4f} | "
            f"{'pass' if row['passed_robust_gate'] else 'fail'} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Ranking artifact: {out_json}")
    print(f"Ranking report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
