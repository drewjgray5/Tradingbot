#!/usr/bin/env python3
"""
Decide whether to promote advisory challenger artifact and optionally activate it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
VALIDATION_DIR = SKILL_DIR / "validation_artifacts"
ARCHIVE_DIR = SKILL_DIR / "artifacts" / "champion_archive"
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from promotion_guard import ensure_signed_approval


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _validate_model(path: Path, strict: bool, promotion: bool) -> bool:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "validate_advisory_model.py"),
        "--model-path",
        str(path),
    ]
    if strict:
        cmd.append("--strict")
    if promotion:
        cmd.append("--promotion")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.strip())
    if proc.stderr:
        print(proc.stderr.strip())
    return proc.returncode == 0


def _resolve_model_path(path_like: str) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (SKILL_DIR / p)


def main() -> int:
    from config import get_advisory_model_path
    from notifier import send_alert
    from promotion_utils import decide_promotion

    parser = argparse.ArgumentParser(description="Decide and optionally promote advisory challenger model")
    parser.add_argument(
        "--challenger-model-path",
        default=str(SKILL_DIR / "artifacts" / "advisory_model_candidate.json"),
    )
    parser.add_argument(
        "--champion-model-path",
        default="",
        help="Optional override for active champion model path",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--promotion", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply model promotion if decision is promote=true")
    parser.add_argument("--notify", action="store_true", help="Send Discord summary alert")
    parser.add_argument("--min-auc-delta", type=float, default=0.005)
    parser.add_argument("--min-top20-delta", type=float, default=0.005)
    parser.add_argument("--max-brier-delta", type=float, default=0.0)
    parser.add_argument("--require-walkforward-gain", action="store_true")
    args = parser.parse_args()
    if not ensure_signed_approval("advisory_model", apply_requested=args.apply):
        return 2

    challenger_path = _resolve_model_path(args.challenger_model_path)
    champion_raw = args.champion_model_path or get_advisory_model_path(SKILL_DIR)
    champion_path = _resolve_model_path(champion_raw)
    if not challenger_path.exists():
        print(f"FAIL: challenger artifact not found: {challenger_path}")
        return 1
    if not champion_path.exists():
        print(f"FAIL: champion artifact not found: {champion_path}")
        return 1

    print("Validating challenger model...")
    challenger_ok = _validate_model(challenger_path, strict=args.strict, promotion=args.promotion)
    print("Validating champion model...")
    champion_ok = _validate_model(champion_path, strict=args.strict, promotion=args.promotion)

    challenger_art = _load_json(challenger_path)
    champion_art = _load_json(champion_path)
    decision = decide_promotion(
        champion=champion_art,
        challenger=challenger_art,
        challenger_gates_passed=challenger_ok,
        min_auc_delta=args.min_auc_delta,
        min_top20_delta=args.min_top20_delta,
        max_brier_delta=args.max_brier_delta,
        require_walkforward_gain=args.require_walkforward_gain,
    )
    try:
        from hypothesis_ledger import promotion_guard_reasons

        hg_reasons = promotion_guard_reasons(SKILL_DIR)
        if hg_reasons:
            decision["hypothesis_promotion_guard"] = hg_reasons
            decision["promote"] = False
            decision["reasons"] = list(decision.get("reasons") or []) + list(hg_reasons)
    except Exception:
        pass
    decision["validate_rc"] = {
        "champion_passed": bool(champion_ok),
        "challenger_passed": bool(challenger_ok),
    }
    decision["paths"] = {
        "champion_model": str(champion_path),
        "challenger_model": str(challenger_path),
    }
    decision["applied"] = False
    decision["archive_path"] = None

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if bool(decision["promote"]) and args.apply:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        archive_path = ARCHIVE_DIR / f"advisory_model_champion_{run_id}.json"
        shutil.copy2(champion_path, archive_path)
        shutil.copy2(challenger_path, champion_path)
        decision["applied"] = True
        decision["archive_path"] = str(archive_path)

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    out_file = VALIDATION_DIR / f"advisory_promotion_decision_{run_id}.json"
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
    }
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Decision artifact: {out_file}")
    print(json.dumps(decision, indent=2))

    if args.notify:
        if decision["promote"] and decision["applied"]:
            send_alert(
                f"Advisory promotion applied. New champion from {challenger_path.name}.",
                kind="success",
                env_path=SKILL_DIR / ".env",
            )
        elif decision["promote"]:
            send_alert(
                "Advisory challenger qualifies for promotion (dry-run mode, not applied).",
                kind="info",
                env_path=SKILL_DIR / ".env",
            )
        else:
            send_alert(
                "Advisory challenger rejected by promotion thresholds.",
                kind="info",
                env_path=SKILL_DIR / ".env",
            )
    return 0 if challenger_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
