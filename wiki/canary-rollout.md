---
source: Brain/Runbooks/Canary Rollout.md
created: 2026-04-13
updated: 2026-04-13
tags: [runbook, canary]
---

# Canary Rollout

> Controlled live testing of new plugins and configuration changes.

## Process

### Phase 1: Shadow (3-5 sessions)
Set plugin to `shadow`, monitor diagnostics for stability.

### Phase 2: Live Canary (1 week)
Promote to `live` for reduced-size canary. Run daily validation.

### Phase 3: Full Rollout
Enable for full production, monitor an additional week.

## Rollback

1. Switch plugin mode back to `off`
2. Re-run validation
3. Verify execution events return to baseline

## Weekly Checklist

- Profit factor trend
- Expectancy non-negative
- Worst drawdown within cap
- Slippage/spread pressure
- Block/resize rates

## Related Pages

- [[plugin-modes]] — plugins to canary
- [[validation]] — validation pipeline
- [[execution-engine]] — execution metrics

---

*Last compiled: 2026-04-13*
