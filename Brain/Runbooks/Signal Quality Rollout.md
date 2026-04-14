---
tags: [runbook, quality]
---
# Signal Quality Rollout

Plan for rolling out quality gates from shadow to enforced mode.

## Source Documentation
Full plan: `schwab_skill/SIGNAL_QUALITY_ROLLOUT.md`

## Rollout Sequence

1. **`QUALITY_GATES_MODE=shadow`** — track would-filter counts, no behavior change
2. **Review diagnostics** — analyze which signals would have been filtered and their outcomes
3. **`QUALITY_GATES_MODE=soft`** — filter only when 2+ weak reasons exist
4. **Monitor** — check signal quality, false positive rate, funnel conversion
5. **`QUALITY_GATES_MODE=hard`** — filter on any single weak reason
6. **Evaluate** — compare signal hit rates pre/post

## Related
- [[Quality Gates]] — quality gate modes and thresholds
- [[Signal Scanner]] — where quality gates are applied
- [[Validation]] — validation pipeline for quality checks
