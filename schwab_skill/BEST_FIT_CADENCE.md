# Best Fit Cadence

Use this schedule to tune parameters without overfitting.

## Daily

- Lock baseline profile using `python scripts/lock_baseline_profile.py`.
- Run a medium resilient batch:
  - `python -u scripts/run_optimization_batch.py --runs 9 --seed-start <N> --rounds 8 --stall-rounds 4 --timeout-seconds 2400 --retry-on-fail 1`
- Generate review output:
  - `python scripts/review_best_fit.py`
- Only adjust paper/shadow settings if classification is `Promote`.

## Weekly

- Run a larger resilient batch:
  - `python -u scripts/run_optimization_batch.py --runs 24 --seed-start <N> --rounds 8 --stall-rounds 4 --timeout-seconds 2400 --retry-on-fail 1`
- Run robustness checks:
  - `python scripts/validate_pf_robustness.py`
- Compare latest best-fit report with prior week before changing `.env`.

## Monthly

- Audit objective/reviewer gate behavior in:
  - `scripts/optimize_strategy_loop.py`
- Revisit scoring weights and drawdown slack only if:
  - live and backtest behavior diverge for multiple weeks, or
  - expected PF stability is not met.

## Promotion Checklist

- Candidate improves (or nearly preserves) net PF across windows.
- Candidate does not worsen max drawdown beyond accepted slack.
- Trade count remains above minimum threshold.
- Improvement repeats across multiple batch runs/seeds.
- If returns rise while PF/drawdown quality degrades, do not promote.
