#!/usr/bin/env python3
"""
Decide/apply strategy parameter promotion from walk-forward artifacts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
CHAMPION_PARAMS_FILE = SKILL_DIR / "artifacts" / "strategy_champion_params.json"
sys.path.insert(0, str(SCRIPTS_DIR))

from promotion_guard import ensure_signed_approval


def _run_validate(cmd_args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(SCRIPTS_DIR / "validate_pf_robustness.py")] + cmd_args
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        print(out)
    if err:
        print(err)
    return proc.returncode, out


def _extract_best_params(path_like: str) -> dict[str, str]:
    p = Path(path_like)
    if not p.is_absolute():
        p = SKILL_DIR / p
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("best_params"), dict):
        return {str(k): str(v) for k, v in data["best_params"].items()}
    raise ValueError("artifact missing best_params")


def _load_selected_artifact_from_ranking(path_like: str) -> str:
    p = Path(path_like)
    if not p.is_absolute():
        p = SKILL_DIR / p
    data = json.loads(p.read_text(encoding="utf-8"))
    selected = data.get("selected", {}) if isinstance(data, dict) else {}
    candidate = str(selected.get("artifact") or "").strip()
    if not candidate:
        raise ValueError("ranking artifact missing selected.artifact")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Decide and optionally apply strategy parameter promotion")
    parser.add_argument("--challenger-artifact", default="", help="Walk-forward optimizer artifact json")
    parser.add_argument("--ranking-artifact", default="", help="Optional ranking artifact to source selected challenger")
    parser.add_argument(
        "--champion-artifact",
        default="",
        help="Optional champion artifact json. Default uses artifacts/strategy_champion_params.json",
    )
    parser.add_argument("--min-pf-delta", type=float, default=0.02)
    parser.add_argument("--min-expectancy-delta", type=float, default=0.0)
    parser.add_argument("--min-oos-pf", type=float, default=1.15)
    parser.add_argument("--min-oos-pf-delta", type=float, default=0.01)
    parser.add_argument("--max-drawdown-degrade-cap", type=float, default=2.0)
    parser.add_argument("--min-trades-threshold", type=int, default=35)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not ensure_signed_approval(
        "strategy_champion_params", apply_requested=args.apply
    ):
        return 2

    challenger_artifact = args.challenger_artifact
    if args.ranking_artifact:
        challenger_artifact = _load_selected_artifact_from_ranking(args.ranking_artifact)
    if not challenger_artifact:
        raise ValueError("Provide --challenger-artifact or --ranking-artifact")

    champion_ref = args.champion_artifact or str(CHAMPION_PARAMS_FILE)
    cmd_args = [
        "--champion-artifact",
        champion_ref,
        "--challenger-artifact",
        challenger_artifact,
        "--min-pf-delta",
        str(args.min_pf_delta),
        "--min-expectancy-delta",
        str(args.min_expectancy_delta),
        "--min-oos-pf",
        str(args.min_oos_pf),
        "--min-oos-pf-delta",
        str(args.min_oos_pf_delta),
        "--max-drawdown-degrade-cap",
        str(args.max_drawdown_degrade_cap),
        "--min-trades-threshold",
        str(args.min_trades_threshold),
    ]
    rc, _stdout = _run_validate(cmd_args)
    promote = rc == 0

    decision = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promote": promote,
        "applied": False,
        "champion_artifact": champion_ref,
        "challenger_artifact": challenger_artifact,
        "ranking_artifact": args.ranking_artifact or None,
        "gates": {
            "min_pf_delta": float(args.min_pf_delta),
            "min_expectancy_delta": float(args.min_expectancy_delta),
            "min_oos_pf": float(args.min_oos_pf),
            "min_oos_pf_delta": float(args.min_oos_pf_delta),
            "max_drawdown_degrade_cap": float(args.max_drawdown_degrade_cap),
            "min_trades_threshold": int(args.min_trades_threshold),
        },
    }
    if promote and args.apply:
        params = _extract_best_params(challenger_artifact)
        CHAMPION_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHAMPION_PARAMS_FILE.write_text(json.dumps({"params": params}, indent=2), encoding="utf-8")
        decision["applied"] = True
        decision["applied_path"] = str(CHAMPION_PARAMS_FILE)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ARTIFACT_DIR / f"strategy_promotion_decision_{run_id}.json"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    print(f"Decision artifact: {out}")
    print(json.dumps(decision, indent=2))
    return 0 if promote else 1


if __name__ == "__main__":
    raise SystemExit(main())
