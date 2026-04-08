# Advisory Model Spec (Phase 1)

Phase 1 runs in advisory-only mode and predicts:

- `p_up_10d`: probability that close at `t+10` is above close at `t`.

No execution path is changed in this phase.

## Canonical Dataset Schema

### Identity / context
- `ticker` (str)
- `entry_date` (ISO datetime)
- `sector_etf` (str or null)
- `breakout_confirmed` (0/1)

### Feature columns
- `signal_score`
- `pct_from_52w_high`
- `avg_vcp_volume_ratio`
- `close_vs_sma50_pct`
- `close_vs_sma200_pct`
- `atr_pct`
- `ret_5d_prev`
- `ret_20d_prev`
- `volume_ratio`
- `sector_rel_21d`
- `sec_risk_score`
- `miro_continuation_prob`
- `miro_bull_trap_prob`

### Label columns (for multi-target roadmap)
- `y_up_5d`
- `y_up_10d` (Phase 1 primary target)
- `y_return_bucket_10d` (`-1`,`0`,`1` for <-2%, -2%..2%, >2%)
- `y_drawdown_gt5_10d`
- `ret_5d_fwd`
- `ret_10d_fwd`
- `drawdown_10d`

## Training + Validation

- Model: logistic regression (L2) + post-hoc probability bin calibration.
- Splits:
  - standard: walk-forward (`3y train`, `6m validation`, `6m test`, rolling by `6m`)
  - promotion: regime-aware walk-forward (`3y train`, `3m validation`, `3m test`, rolling by `3m`)
- Candidate path:
  - baseline: logistic on base features
  - upgraded (optional): interaction logistic on base + interaction features
- Calibrated metrics tracked:
  - Brier score
  - AUC
  - Top-20% hit rate
  - Precision / recall / accuracy
  - Calibration monotonicity violations and worst drop

## Runtime Signal Contract

Scanner augments each signal with:

- `advisory.p_up_10d`
- `advisory.p_up_10d_raw`
- `advisory.confidence_bucket` (`high|medium|low`)
- `advisory.expected_move_10d`
- `advisory.model_version`
- `advisory.reasoning`

## Acceptance Gates

Default gates (artifact-encoded and validated by script):

- Standard:
  - `min_fold_count >= 2`
  - `calibration_brier <= 0.255`
  - `calibration_auc >= 0.52`
  - `top20_hit_rate >= 0.52`
- Promotion:
  - `min_fold_count >= 6`
  - `min_fold_auc >= 0.52`
  - `fold_auc_std <= 0.05`
  - `top10_hit_rate_per_fold >= 0.52`
  - `regime_count >= 2`
  - `calibration_violations <= 1`
  - `calibration_worst_drop <= 0.08`

Validation command:

`python scripts/validate_advisory_model.py --strict --promotion`

## Champion/Challenger Automation

Weekly cycle:

1. Train challenger artifact (`artifacts/advisory_model_candidate.json`)
2. Validate challenger and champion with identical strict/promotion gates
3. Compare core metrics and compute deltas
4. Promote only if all thresholds pass; otherwise retain champion
5. Persist decision artifact under `validation_artifacts/`

Commands:

- `python scripts/train_and_evaluate_challenger.py --profile promotion --allow-model-upgrades --strict --promotion`
- `python scripts/decide_and_promote_advisory_model.py --strict --promotion`
- `python scripts/decide_and_promote_advisory_model.py --strict --promotion --apply --notify`

Default promotion thresholds:

- `calibration_auc_delta >= 0.005`
- `calibration_top20_hit_rate_delta >= 0.005`
- `calibration_brier_delta <= 0.0` (challenger cannot be worse)
- Optional walk-forward deltas required non-negative when enabled

