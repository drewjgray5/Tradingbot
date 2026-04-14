---
tags: [runbook, validation]
---
# Validation

Unified validation pipeline for ensuring system correctness.

## Source Documentation
- Validation matrix: `schwab_skill/VALIDATION_MATRIX.md`
- Validation runbook: `schwab_skill/VALIDATION_RUNBOOK.md`

## Quick Start

### Full validation (local)
```
python scripts/validate_all.py --profile local --strict
```

### Hardening (skip backtest)
```
python scripts/validate_all.py --profile local --strict --skip-backtest
```

### CI fast checks (PR/push)
```
python scripts/validate_signal_quality.py
python scripts/validate_scanner_parallelization.py
python scripts/validate_shadow_mode.py
```

### CI artifact-producing
```
python scripts/validate_all.py --profile ci --skip-backtest --strict
```

### Server heavy cycle (nightly/weekly)
```
python scripts/run_continuous_strategy_cycle.py --strict
```

## Validation Artifacts
Written to `schwab_skill/validation_artifacts/`:
- `continuous_validation_status.json` — current status
- `validate_all_*.json` — per-run summaries

## Dashboard Exposure
- `GET /api/status` -> includes `validation_status`
- `GET /api/validation/status` -> detailed status

## Related
- [[Operations MOC]] — operations overview
- [[Canary Rollout]] — uses validation during canary
- [[Signal Quality Rollout]] — quality-specific validation
