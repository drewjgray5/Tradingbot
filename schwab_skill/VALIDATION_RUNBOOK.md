# Validation Runbook

Use this to execute the same validation flow in each environment.

## Local Interactive (Windows/Mac)

1. `python scripts/validate_all.py --profile local --strict`
2. Optional web metrics gate:
   - Start web app: `uvicorn webapp.main:app --port 8000`
   - Run: `python scripts/validate_all.py --profile local --web-base-url http://127.0.0.1:8000 --strict`

Expected:
- All steps `PASS`
- Summary artifact written under `validation_artifacts/`

## Headless Linux Server

1. Ensure timezone data installed and scheduler host clock synced.
2. `python scripts/validate_all.py --profile server --strict`

Expected:
- Healthcheck and observability gates pass.
- No circuit instability.

## Container Runtime

1. Start container with required env and writable volume for runtime artifacts.
2. `python scripts/validate_all.py --profile container --strict`

Expected:
- Contract checks and baseline backtest pass.
- Artifact generated inside mounted volume.

## CI Pipeline

1. `python scripts/validate_all.py --profile ci --strict`
   - Optional fast mode: `python scripts/validate_all.py --profile ci --skip-backtest --strict`

Expected:
- No secret-dependent checks required by default.
- Fast deterministic checks pass before merge.

## Continuous Improvement Cadence (Hybrid)

Use this split to keep PR feedback fast while still running heavy improvement loops:

1. **PR/Push CI (quick)**  
   - `python -m ruff check .`  
   - `python -m pytest -q`  
   - `python -m mypy`  
   - `python scripts/validate_signal_quality.py`  
   - `python scripts/validate_scanner_parallelization.py`  
   - `python scripts/validate_shadow_mode.py`

2. **Scheduled CI (artifact-producing, weekdays)**
   - `python scripts/validate_all.py --profile ci --skip-backtest --strict --max-parallel 4 --baseline latest --fail-on-regression`
   - Restores the previous run's `latest_validation_report.json` from
     the GitHub Actions cache, runs the suite in parallel, and exits
     non-zero if any step that was passing yesterday is failing today.
   - Writes a stable diff artifact at
     `validation_artifacts/latest_baseline_delta.json` for downstream
     dashboards / Slack alerts.
   - Upload `validation_artifacts/`

3. **Server heavy cycle (nightly/weekly)**  
   - `python scripts/run_continuous_strategy_cycle.py --strict`
   - Writes `validation_artifacts/continuous_validation_status.json`

4. **Manual promotion only**
   - Any `--apply` flow requires a fresh signed entry in
     `scripts/promotion_ledger.jsonl`:
     ```
     python scripts/promotion_ledger.py append --target <name> --reason "<why>"
     python scripts/promotion_ledger.py check  --target <name>   # optional pre-flight
     ```
   - Approval expires 24h after the ledger entry is written.
   - Legacy `MANUAL_PROMOTION_APPROVED=1` still works during the
     transition (with a deprecation warning) but should be replaced by
     ledger entries.
   - Without either, promotion scripts return non-zero and do not apply.

## Troubleshooting

- If `healthcheck.py` fails: refresh tokens via OAuth flow.
- If observability gate fails: inspect `execution_safety_metrics.json` and web deep health metrics.
- If smoke fails in web health: verify dependencies (`fastapi`, `uvicorn`, `sqlalchemy`) and local DB access.

