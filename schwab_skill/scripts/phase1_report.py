"""
Generate the Phase 1 markdown summary from the per-config result files
written by ``phase1_overlay_sweep.py``.

Outputs:
  validation_artifacts/phase1_overlay_sweep_<timestamp>.md

Includes:
  * One-line per-config table: PF mean (control vs treatment), PF mean delta,
    worst-era PF, thin eras, regressed eras, ship/iterate/discard verdict.
  * Per-config detail block with the 5-era PF / DD / return table and the
    overlay env-var diff vs control.
  * Pareto-best stack proposal (highest PF mean delta with no thin / regressed
    eras).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
RESULTS_DIR = ARTIFACT_DIR / "phase1_results"


def _verdict(summary: dict[str, Any]) -> str:
    delta = float(summary.get("pf_mean_delta") or 0.0)
    if not summary.get("passes_guardrails"):
        return "discard (failed guardrails)"
    if delta > 0.10:
        return "ship"
    if delta > 0.0:
        return "iterate"
    return "discard"


def _format_pf(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _format_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    if not RESULTS_DIR.exists():
        print(f"No results directory: {RESULTS_DIR}")
        return 1
    payloads: list[dict[str, Any]] = []
    for p in sorted(RESULTS_DIR.glob("*.json")):
        try:
            payloads.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"skipping {p}: {exc}")
    if not payloads:
        print("No result files yet.")
        return 1

    control = next((p for p in payloads if p["config_id"] == "control"), None)
    if control is None:
        print("Warning: no control result; deltas will be missing.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = ARTIFACT_DIR / f"phase1_overlay_sweep_{timestamp}.md"
    lines: list[str] = []
    lines.append("# Phase 1 — Intelligence-overlay sweep summary")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_  ")
    lines.append(f"_Configs evaluated: {len(payloads)}_  ")
    lines.append("_Universe: full Schwab watchlist (2,923 tickers)_  ")
    lines.append("_Backtest harness: `run_multi_era_backtest_schwab_only.py` per config; per-era PF/DD/return from Phase 0 portfolio simulator_  ")
    lines.append("")

    lines.append("## Ranking (Pareto on PF mean delta vs control)")
    lines.append("")
    lines.append("| config_id | description | pf_mean (treatment) | pf_mean Δ | worst-era pf | thin eras | regressed eras (>0.10) | verdict |")
    lines.append("|---|---|---:|---:|---:|---|---|---|")
    ranked = sorted(
        payloads,
        key=lambda d: (
            -(d["summary"].get("pf_mean_delta") or 0.0),
            -(float(d["summary"].get("worst_era_pf_treatment") or 0.0)),
        ),
    )
    for p in ranked:
        s = p["summary"]
        lines.append(
            "| {cid} | {desc} | {pf_t} | {pf_d:+.3f} | {worst} | {thin} | {regr} | {verdict} |".format(
                cid=p["config_id"],
                desc=p["description"],
                pf_t=_format_pf(s.get("pf_mean_treatment")),
                pf_d=float(s.get("pf_mean_delta") or 0.0),
                worst=_format_pf(s.get("worst_era_pf_treatment")),
                thin=", ".join(s.get("thin_eras") or []) or "—",
                regr=", ".join(f"{r['era']}({r['pf_delta']:+.2f})" for r in (s.get("regressed_eras") or [])) or "—",
                verdict=_verdict(s),
            )
        )
    lines.append("")

    if control:
        lines.append("## Per-config detail")
        lines.append("")
        for p in ranked:
            s = p["summary"]
            lines.append(f"### `{p['config_id']}` — {p['description']}")
            lines.append("")
            lines.append(f"**Verdict:** {_verdict(s)}  ")
            lines.append(f"**PF mean treatment:** {_format_pf(s.get('pf_mean_treatment'))}  ")
            lines.append(f"**PF mean Δ vs control:** {float(s.get('pf_mean_delta') or 0.0):+.3f}  ")
            lines.append(f"**Worst-era PF (treatment):** {_format_pf(s.get('worst_era_pf_treatment'))}  ")
            lines.append("")
            lines.append("| era | trades | win% net | pf control | pf treatment | pf Δ | dd control | dd treatment | ret control | ret treatment |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
            for r in s.get("rows", []):
                lines.append(
                    "| {era} | {n} | {wr} | {pf_c} | {pf_t} | {pf_d} | {dd_c} | {dd_t} | {ret_c} | {ret_t} |".format(
                        era=r["era"],
                        n=r.get("trades"),
                        wr=r.get("win_rate_net"),
                        pf_c=_format_pf(r.get("pf_control")),
                        pf_t=_format_pf(r.get("pf_treatment")),
                        pf_d=_format_pf(r.get("pf_delta")),
                        dd_c=_format_pct(r.get("dd_control")),
                        dd_t=_format_pct(r.get("dd_treatment")),
                        ret_c=_format_pct(r.get("ret_control")),
                        ret_t=_format_pct(r.get("ret_treatment")),
                    )
                )
            lines.append("")
            lines.append("**Env-var diff vs control:**")
            lines.append("")
            ctrl_env = control["env"]
            diff_lines = []
            for k, v in p["env"].items():
                cval = ctrl_env.get(k)
                if str(cval) != str(v):
                    diff_lines.append(f"- `{k}`: `{cval}` → `{v}`")
            if diff_lines:
                lines.extend(diff_lines)
            else:
                lines.append("- (none — config equals control)")
            lines.append("")

    # Pareto stack proposal: take the Pareto-best from each overlay family.
    stack_picks: dict[str, dict[str, Any]] = {}
    for p in ranked:
        if not p["summary"].get("passes_guardrails"):
            continue
        if (p["summary"].get("pf_mean_delta") or 0.0) <= 0:
            continue
        cid = p["config_id"]
        family = (
            "exit_manager" if cid.startswith("exit_") else
            "event_risk" if cid.startswith("event_") else
            "meta_policy" if cid == "meta_policy_live" else
            "exec_quality" if cid == "exec_quality_live" else
            "other"
        )
        if family in stack_picks:
            continue
        stack_picks[family] = p

    lines.append("## Proposed stacked configuration")
    lines.append("")
    if stack_picks:
        lines.append("Pareto-best per overlay family that improved PF mean and passed guardrails:")
        lines.append("")
        for family, p in stack_picks.items():
            lines.append(f"- **{family}** → `{p['config_id']}` (PF Δ {float(p['summary']['pf_mean_delta']):+.3f})")
        lines.append("")
        lines.append("Stacked env-var set:")
        lines.append("")
        lines.append("```")
        merged: dict[str, str] = {}
        for p in stack_picks.values():
            for k, v in p["env"].items():
                merged[k] = v
        for k in sorted(merged):
            lines.append(f"{k}={merged[k]}")
        lines.append("```")
    else:
        lines.append("No overlay family produced a positive PF delta with passing guardrails. **Recommendation: discard the entire intelligence overlay stack** for the current strategy and proceed to Phase 2 (regime suppression).")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
