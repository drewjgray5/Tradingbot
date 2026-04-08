#!/usr/bin/env python3
"""
Train challenger advisory artifact and compare against current champion.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
ARTIFACTS_DIR = SKILL_DIR / "artifacts"
VALIDATION_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SKILL_DIR))


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(SKILL_DIR),
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def main() -> int:
    from config import get_advisory_model_path
    from promotion_utils import compare_artifacts

    parser = argparse.ArgumentParser(description="Train and evaluate advisory challenger artifact")
    parser.add_argument("--profile", choices=["standard", "promotion"], default="promotion")
    parser.add_argument("--allow-model-upgrades", action="store_true")
    parser.add_argument("--max-tickers", type=int, default=250)
    parser.add_argument("--strict", action="store_true", help="Require strict validation for challenger/champion")
    parser.add_argument("--promotion", action="store_true", help="Run promotion-grade validation checks")
    parser.add_argument(
        "--challenger-model-out",
        default=str(ARTIFACTS_DIR / "advisory_model_candidate.json"),
    )
    parser.add_argument(
        "--challenger-dataset-out",
        default=str(VALIDATION_DIR / "advisory_dataset_candidate.csv"),
    )
    args = parser.parse_args()

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    challenger_model = Path(args.challenger_model_out)
    champion_path_raw = get_advisory_model_path(SKILL_DIR)
    champion_path = Path(champion_path_raw)
    if not champion_path.is_absolute():
        champion_path = SKILL_DIR / champion_path

    train_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "train_advisory_model.py"),
        "--profile",
        args.profile,
        "--max-tickers",
        str(args.max_tickers),
        "--dataset-out",
        str(Path(args.challenger_dataset_out)),
        "--model-out",
        str(challenger_model),
    ]
    if args.allow_model_upgrades:
        train_cmd.append("--allow-model-upgrades")

    print("Training challenger artifact...")
    train_rc, train_out, train_err = _run(train_cmd)
    if train_out:
        print(train_out)
    if train_err:
        print(train_err)
    if train_rc != 0:
        print("FAIL: challenger training failed")
        return 1

    validate_base = [sys.executable, str(SCRIPTS_DIR / "validate_advisory_model.py"), "--model-path"]
    challenger_validate_cmd = validate_base + [str(challenger_model)]
    champion_validate_cmd = validate_base + [str(champion_path)]
    if args.strict:
        challenger_validate_cmd.append("--strict")
        champion_validate_cmd.append("--strict")
    if args.promotion:
        challenger_validate_cmd.append("--promotion")
        champion_validate_cmd.append("--promotion")

    print("Validating challenger artifact...")
    c_val_rc, c_val_out, c_val_err = _run(challenger_validate_cmd)
    if c_val_out:
        print(c_val_out)
    if c_val_err:
        print(c_val_err)

    print("Validating champion artifact...")
    p_val_rc, p_val_out, p_val_err = _run(champion_validate_cmd)
    if p_val_out:
        print(p_val_out)
    if p_val_err:
        print(p_val_err)

    champion_art = _load_json(champion_path)
    challenger_art = _load_json(challenger_model)
    comparison = compare_artifacts(champion_art, challenger_art)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "strict": bool(args.strict),
        "promotion_checks": bool(args.promotion),
        "allow_model_upgrades": bool(args.allow_model_upgrades),
        "paths": {
            "champion_model": str(champion_path),
            "challenger_model": str(challenger_model),
        },
        "validate_rc": {
            "challenger": c_val_rc,
            "champion": p_val_rc,
        },
        "comparison": comparison,
        "gates": {
            "challenger_passed": c_val_rc == 0,
            "champion_passed": p_val_rc == 0,
        },
    }
    out_file = VALIDATION_DIR / f"challenger_compare_{run_id}.json"
    out_file.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")
    print(f"Comparison artifact: {out_file}")

    if c_val_rc != 0:
        print("FAIL: challenger did not pass validation gates")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
