---
source: Brain/Runbooks/Validation.md
created: 2026-04-13
updated: 2026-04-13
tags: [runbook, validation]
---

# Validation

> Unified validation pipeline for system correctness.

## Quick Start

```bash
# Full validation
python scripts/validate_all.py --profile local --strict

# CI fast checks
python scripts/validate_signal_quality.py
python scripts/validate_scanner_parallelization.py
python scripts/validate_shadow_mode.py

# Nightly heavy cycle
python scripts/run_continuous_strategy_cycle.py --strict
```

## Artifacts

Written to `schwab_skill/validation_artifacts/`:
- `continuous_validation_status.json`
- `validate_all_*.json`

## Dashboard

- `GET /api/status` includes `validation_status`
- `GET /api/validation/status` for detailed status

## Related Pages

- [[canary-rollout]] — validation during canary
- [[signal-quality-rollout]] — quality-specific validation

---

*Last compiled: 2026-04-13*
