# Cross-Environment Validation Matrix

This matrix defines minimum pass/fail gates for each runtime target.

## Profiles

| Profile | Goal | Required Command |
|---|---|---|
| `local` | Full developer confidence before manual testing | `python scripts/validate_all.py --profile local --strict` |
| `server` | Headless production-host readiness | `python scripts/validate_all.py --profile server --strict` |
| `container` | Image/runtime portability checks | `python scripts/validate_all.py --profile container --strict` |
| `ci` | Fast deterministic pre-merge safety checks | `python scripts/validate_all.py --profile ci --strict` |

## Fast vs Heavy Separation

| Lane | Purpose | Command(s) |
|---|---|---|
| CI quick | Fast PR/push safety | `ruff`, `pytest`, `mypy`, `validate_signal_quality.py`, `validate_scanner_parallelization.py`, `validate_shadow_mode.py` |
| CI scheduled | Frequent artifact snapshots without heavy backtest | `python scripts/validate_all.py --profile ci --skip-backtest --strict` |
| Server heavy | Continuous improvement/tuning/backtest | `python scripts/run_continuous_strategy_cycle.py --strict` |

Fast mode (when network/data providers are constrained):

- `python scripts/validate_all.py --profile ci --skip-backtest --strict`
- `python scripts/validate_all.py --profile ci --skip-backtest --pf-robust --strict`

Promotion-grade mode:

- `python scripts/validate_all.py --profile ci --promotion --strict`

## Gate Definitions

### 1) Static and contract checks
- `scripts/validate_signal_quality.py`
- `scripts/validate_scanner_parallelization.py`
- `scripts/validate_ui_payloads.py`
- `scripts/validate_shadow_mode.py`
- `scripts/validate_advisory_model.py`
- `scripts/validate_promotion_flow.py`
- `scripts/validation_smoke.py`

Pass condition:
- Exit code `0` on all scripts.
- Advisory gate is optional by default; set `ADVISORY_REQUIRE_MODEL=true` (or run `--strict`) to hard-fail when artifact/gates are missing.
- Promotion mode enforces fold consistency, regime coverage, monotonic calibration, and per-fold top-bucket stability.

### 2) Service smoke checks
- `healthcheck.py` (local/server only)
- `webapp/main.py` health contract covered via `scripts/validation_smoke.py`

Pass condition:
- Tokens present and heartbeat path executes.
- `/api/health` contract returns `ok=true` and `status=ok`.

### 3) Execution safety (shadow path)
- `scripts/validate_shadow_mode.py`
- `scripts/validate_observability_gates.py`

Pass condition:
- Shadow-mode order path does not submit live broker requests.
- Circuits stable, stop-failure and guardrail thresholds within configured limits.

### 4) Backtest regression (CI/container baseline)
- `scripts/validate_backtest.py --tickers 20`
- `scripts/validate_pf_robustness.py`

Pass condition:
- Script exits `0`.
- Optional regression warning can be enabled by adding `--warn-on-regression`.
- Promotion mode (`--promotion`) applies conservative costs and net-performance/risk gates.
- PF robustness mode (`--pf-robust`) requires stable net PF and drawdown behavior across multiple windows.

### 5) Promotion pipeline safety checks
- `scripts/train_and_evaluate_challenger.py` (artifact + gate parity vs champion)
- `scripts/decide_and_promote_advisory_model.py` (dry-run by default)

Pass condition:
- Dry-run decision artifact is emitted with explicit promote/no-promote rationale.
- Apply mode archives current champion before activation.

## Optional Web Metrics Gate

If the web API is running, include endpoint error-rate checks:

`python scripts/validate_all.py --profile local --web-base-url http://127.0.0.1:8000 --strict`

This enables `/api/health/deep` metric gating inside `validate_observability_gates.py`.

## Artifact Contract

Each run writes a machine-readable summary:

- Directory: `validation_artifacts/`
- File pattern: `validate_all_<UTC timestamp>_<profile>.json`
- Continuous status: `validation_artifacts/continuous_validation_status.json`

Promotion safety contract:
- `--apply` actions require a fresh signed entry in
  `scripts/promotion_ledger.jsonl` (see README → "Promotion Guard").
- `MANUAL_PROMOTION_APPROVED=1` is accepted as a deprecated fallback.
- Default scheduler behavior is report-only (no apply).

Use these artifacts to compare results across local, CI, and server runs.

