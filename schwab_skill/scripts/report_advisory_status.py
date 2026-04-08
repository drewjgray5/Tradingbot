#!/usr/bin/env python3
"""
Emit current champion advisory model status and optional Discord alert.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VALIDATION_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SKILL_DIR))


def _latest_decision_file() -> Path | None:
    files = sorted(VALIDATION_DIR.glob("advisory_promotion_decision_*.json"))
    return files[-1] if files else None


def main() -> int:
    from advisory_model import load_model_artifact
    from config import get_advisory_model_path
    from notifier import send_alert
    from promotion_utils import extract_metrics

    parser = argparse.ArgumentParser(description="Report champion advisory model metrics")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()

    model_path = get_advisory_model_path(SKILL_DIR)
    model = load_model_artifact(skill_dir=SKILL_DIR, path=model_path)
    if not model:
        print("FAIL: active advisory model artifact not found")
        return 1
    m = extract_metrics(model)
    payload = {
        "model_version": model.get("model_version"),
        "training_profile": model.get("training_profile"),
        "model_selected": model.get("model_selected"),
        "metrics": m,
    }

    latest_decision = _latest_decision_file()
    if latest_decision:
        try:
            decision_data = json.loads(latest_decision.read_text(encoding="utf-8"))
            payload["latest_promotion_decision"] = {
                "file": str(latest_decision),
                "promote": ((decision_data.get("decision") or {}).get("promote")),
                "applied": ((decision_data.get("decision") or {}).get("applied")),
            }
        except Exception:
            pass

    print(json.dumps(payload, indent=2))
    if args.notify:
        msg = (
            f"Advisory champion `{payload['model_version']}` "
            f"(profile={payload['training_profile']}) "
            f"AUC={m['calibration_auc']:.4f} "
            f"Brier={m['calibration_brier']:.4f} "
            f"Top20={m['calibration_top20_hit_rate']:.4f}"
        )
        send_alert(msg, kind="info", env_path=SKILL_DIR / ".env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
