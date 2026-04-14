---
source: Brain/Runbooks/Signal Quality Rollout.md
created: 2026-04-13
updated: 2026-04-13
tags: [runbook, quality]
---

# Signal Quality Rollout

> Plan for rolling out quality gates from shadow to enforced mode.

## Sequence

1. `QUALITY_GATES_MODE=shadow` — track would-filter counts
2. Review diagnostics — analyze which signals would have been filtered
3. `QUALITY_GATES_MODE=soft` — filter when 2+ weak reasons
4. Monitor signal quality and false positive rate
5. `QUALITY_GATES_MODE=hard` — filter on any single weak reason
6. Evaluate hit rates pre/post

## Related Pages

- [[quality-gates]] — modes and thresholds
- [[signal-scanner]] — where gates are applied
- [[validation]] — validation pipeline

---

*Last compiled: 2026-04-13*
