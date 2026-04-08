#!/usr/bin/env python3
"""
Validate advisory model artifact and gate metrics.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    from advisory_model import load_model_artifact
    from config import get_advisory_require_model

    parser = argparse.ArgumentParser(description="Validate advisory model artifact and acceptance gates")
    parser.add_argument(
        "--model-path",
        default="",
        help="Optional model artifact path override (relative to skill dir or absolute)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on any missing metric/gate mismatch",
    )
    parser.add_argument(
        "--promotion",
        action="store_true",
        help="Enforce promotion-grade fold consistency/regime/monotonicity gates.",
    )
    args = parser.parse_args()

    model = load_model_artifact(skill_dir=SKILL_DIR, path=(args.model_path or None))
    require_model = bool(get_advisory_require_model(SKILL_DIR))
    if not model:
        if require_model or args.strict:
            print("FAIL: advisory model artifact not found.")
            return 1
        print("PASS: advisory model not present (optional in non-strict mode).")
        return 0

    errors: list[str] = []
    required_top = ["model_version", "feature_columns", "coef", "intercept", "calibration_bins", "walk_forward", "calibration_metrics"]
    for key in required_top:
        if key not in model:
            errors.append(f"missing key: {key}")

    wf = model.get("walk_forward") or {}
    cal = model.get("calibration_metrics") or {}
    gates = model.get("acceptance_gates") or {}
    fold_count = int(wf.get("fold_count", 0) or 0)
    min_fold = int(gates.get("min_fold_count", 0) or 0)
    max_brier = float(gates.get("max_brier", 1.0) or 1.0)
    min_auc = float(gates.get("min_auc", 0.0) or 0.0)
    min_top = float(gates.get("min_top20_hit_rate", 0.0) or 0.0)
    brier = float(cal.get("brier", 1.0) or 1.0)
    auc = float(cal.get("auc", 0.0) or 0.0)
    top_hit = float(cal.get("top20_hit_rate", 0.0) or 0.0)

    if fold_count < min_fold:
        errors.append(f"fold_count {fold_count} < min_fold_count {min_fold}")
    if brier > max_brier:
        errors.append(f"calibration brier {brier:.4f} > max_brier {max_brier:.4f}")
    if auc < min_auc:
        errors.append(f"calibration auc {auc:.4f} < min_auc {min_auc:.4f}")
    if top_hit < min_top:
        errors.append(f"top20_hit_rate {top_hit:.4f} < min_top20_hit_rate {min_top:.4f}")

    folds = list(wf.get("folds") or [])
    regime_counts = ((wf.get("summary") or {}).get("regime_counts") or {})
    min_fold_auc = float(gates.get("min_fold_auc", 0.52 if args.promotion else 0.0) or (0.52 if args.promotion else 0.0))
    max_fold_auc_std = float(gates.get("max_fold_auc_std", 0.05 if args.promotion else 1.0) or (0.05 if args.promotion else 1.0))
    min_top10_per_fold = float(
        gates.get("min_top10_hit_rate_per_fold", 0.52 if args.promotion else 0.0) or (0.52 if args.promotion else 0.0)
    )
    min_regime_count = int(gates.get("min_regime_count", 2 if args.promotion else 1) or (2 if args.promotion else 1))
    max_calib_viol = int(gates.get("max_calibration_violations", 1 if args.promotion else 999) or (1 if args.promotion else 999))
    max_calib_drop = float(
        gates.get("max_calibration_worst_drop", 0.08 if args.promotion else 1.0) or (0.08 if args.promotion else 1.0)
    )
    calib_monot = model.get("calibration_monotonicity") or {}

    if args.promotion:
        if len([k for k, v in regime_counts.items() if int(v) > 0]) < min_regime_count:
            errors.append(
                f"regime coverage insufficient: have {len([k for k, v in regime_counts.items() if int(v) > 0])}, need {min_regime_count}"
            )
        fold_aucs = []
        for f in folds:
            fm = f.get("metrics") or {}
            auc_i = float(fm.get("auc", 0.0) or 0.0)
            top10_i = float(fm.get("top10_hit_rate", fm.get("top20_hit_rate", 0.0)) or 0.0)
            mono_i = f.get("calibration_monotonicity") or {}
            viol_i = int(mono_i.get("violations", 0) or 0)
            drop_i = float(mono_i.get("worst_drop", 0.0) or 0.0)
            fold_aucs.append(auc_i)
            if auc_i < min_fold_auc:
                errors.append(f"fold[{f.get('fold_idx')}] auc {auc_i:.4f} < min_fold_auc {min_fold_auc:.4f}")
            if top10_i < min_top10_per_fold:
                errors.append(
                    f"fold[{f.get('fold_idx')}] top10_hit_rate {top10_i:.4f} < min_top10_hit_rate_per_fold {min_top10_per_fold:.4f}"
                )
            if viol_i > max_calib_viol:
                errors.append(
                    f"fold[{f.get('fold_idx')}] calibration violations {viol_i} > max_calibration_violations {max_calib_viol}"
                )
            if drop_i > max_calib_drop:
                errors.append(
                    f"fold[{f.get('fold_idx')}] calibration worst_drop {drop_i:.4f} > max_calibration_worst_drop {max_calib_drop:.4f}"
                )
        if fold_aucs:
            auc_std = float((sum((x - (sum(fold_aucs) / len(fold_aucs))) ** 2 for x in fold_aucs) / len(fold_aucs)) ** 0.5)
            if auc_std > max_fold_auc_std:
                errors.append(f"fold auc std {auc_std:.4f} > max_fold_auc_std {max_fold_auc_std:.4f}")
        if int(calib_monot.get("violations", 0) or 0) > max_calib_viol:
            errors.append(
                f"global calibration violations {int(calib_monot.get('violations', 0) or 0)} > max_calibration_violations {max_calib_viol}"
            )
        if float(calib_monot.get("worst_drop", 0.0) or 0.0) > max_calib_drop:
            errors.append(
                f"global calibration worst_drop {float(calib_monot.get('worst_drop', 0.0) or 0.0):.4f} > max_calibration_worst_drop {max_calib_drop:.4f}"
            )

    if errors and args.strict:
        print("FAIL: advisory model validation failed:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("PASS: advisory model validation checks")
    print(f"  model_version={model.get('model_version')}")
    print(f"  training_profile={model.get('training_profile')}")
    print(f"  model_selected={model.get('model_selected')}")
    print(f"  fold_count={fold_count}")
    print(f"  calibration_auc={auc:.4f}")
    print(f"  calibration_brier={brier:.4f}")
    print(f"  top20_hit_rate={top_hit:.4f}")
    if errors:
        print("WARN: non-strict mismatches:")
        for e in errors:
            print(f"  - {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

