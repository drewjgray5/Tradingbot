#!/usr/bin/env python3
"""
Train advisory model for P(up over next 10 trading days).

Outputs:
- dataset CSV (for reproducibility and diagnostics)
- model artifact JSON (used by scanner/web UI)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _default_start() -> str:
    return (datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d")


def main() -> int:
    from advisory_model import build_advisory_dataset, save_model_artifact, train_advisory_model

    parser = argparse.ArgumentParser(description="Train advisory P(up_10d) model")
    parser.add_argument("--start-date", default=_default_start(), help="Dataset start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"), help="Dataset end date (YYYY-MM-DD)")
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker override")
    parser.add_argument("--max-tickers", type=int, default=250, help="Limit watchlist size for faster runs")
    parser.add_argument(
        "--profile",
        choices=["standard", "promotion"],
        default="standard",
        help="Training profile; promotion enforces denser walk-forward regime checks.",
    )
    parser.add_argument(
        "--allow-model-upgrades",
        action="store_true",
        help="Enable upgraded candidate path (interaction logistic) in addition to baseline logistic.",
    )
    parser.add_argument(
        "--dataset-out",
        default=str(SKILL_DIR / "validation_artifacts" / "advisory_dataset_latest.csv"),
        help="Path for generated dataset CSV",
    )
    parser.add_argument(
        "--model-out",
        default=str(SKILL_DIR / "artifacts" / "advisory_model_v1.json"),
        help="Path for model artifact JSON",
    )
    parser.add_argument("--min-rows", type=int, default=500, help="Fail if dataset rows below this threshold")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    print("Building advisory dataset...")
    ds = build_advisory_dataset(
        skill_dir=SKILL_DIR,
        tickers=tickers,
        start_date=args.start_date,
        end_date=args.end_date,
        max_tickers=args.max_tickers,
    )
    if ds.empty or len(ds) < args.min_rows:
        print(f"FAIL: dataset too small (rows={len(ds)} min_required={args.min_rows})")
        return 1

    ds_out = Path(args.dataset_out)
    ds_out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_csv(ds_out, index=False)
    print(f"Dataset saved: {ds_out} (rows={len(ds)})")

    print("Training advisory model...")
    artifact = train_advisory_model(
        ds,
        target_col="y_up_10d",
        profile=args.profile,
        allow_model_upgrades=bool(args.allow_model_upgrades),
    )
    model_out = save_model_artifact(artifact, skill_dir=SKILL_DIR, path=args.model_out)

    wf = artifact.get("walk_forward", {})
    summary = wf.get("summary", {})
    cal = artifact.get("calibration_metrics", {})
    preview = {
        "model_version": artifact.get("model_version"),
        "training_profile": artifact.get("training_profile"),
        "model_selected": artifact.get("model_selected"),
        "rows_total": (artifact.get("training_summary") or {}).get("rows_total"),
        "fold_count": wf.get("fold_count"),
        "calibration_auc": cal.get("auc"),
        "calibration_brier": cal.get("brier"),
        "walkforward_auc_mean": ((summary.get("auc") or {}).get("mean") if isinstance(summary.get("auc"), dict) else None),
        "walkforward_top20_hit_rate_mean": (
            (summary.get("top20_hit_rate") or {}).get("mean")
            if isinstance(summary.get("top20_hit_rate"), dict)
            else None
        ),
        "model_path": str(model_out),
    }
    print("Training summary:")
    print(json.dumps(preview, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

