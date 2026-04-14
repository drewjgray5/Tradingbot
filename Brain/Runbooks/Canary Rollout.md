---
tags: [runbook, canary]
---
# Canary Rollout

Controlled live testing of new plugins and configuration changes.

## Source Documentation
Full canary procedures: `schwab_skill/CANARY_RUNBOOK.md`

## Canary Process

### Phase 1: Shadow (3-5 sessions)
1. Set plugin mode to `shadow` (e.g. `EXEC_QUALITY_MODE=shadow`)
2. Run normally for 3-5 trading sessions
3. Monitor diagnostics for stability (no sharp jump in block/error rates)

### Phase 2: Live Canary (1 week)
1. Promote to `live` for a reduced-size canary account/session
2. Run daily validation: `python scripts/validate_all.py --profile local --strict --skip-backtest`
3. Monitor execution events vs baseline

### Phase 3: Full Rollout
1. If canary week passes cleanly, enable for full production
2. Continue monitoring for an additional week

## Rollback Procedure
1. Switch affected plugin mode back to `off`
2. Re-run validation pipeline
3. Verify execution events return to baseline
4. If strategy promotion was applied, restore prior champion params/artifact

## Weekly Operational Checklist
During canary/rollout, review weekly:
- Profit factor (`profit_factor_net`) trend
- Expectancy (`avg_return_net_pct`) non-negative
- Worst drawdown (`max_drawdown_net_pct`) within cap
- Slippage/spread pressure
- Block/resize rates (event risk, regime v2, guardrails)

## Related
- [[Plugin Modes]] — plugins to canary
- [[Validation]] — validation pipeline to run during canary
- [[Execution Engine]] — monitors execution metrics
