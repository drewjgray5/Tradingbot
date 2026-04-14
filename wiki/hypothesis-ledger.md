---
source: Brain/Strategies/Hypothesis Ledger.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, calibration, learning]
---

# Hypothesis Ledger

> Tracks decision quality separately from P&L to calibrate predictive accuracy.

## Why Separate from P&L

P&L conflates signal quality with execution, sizing, and timing. The ledger isolates: "When the system said UP, did it actually go up?"

## Workflow

1. **Record**: signal alert → row in `.hypothesis_ledger.json` (ticker, direction, reference price, levels)
2. **Score**: `python scripts/score_hypothesis_outcomes.py` checks at T+1, T+5, T+20 trading days
3. **Review**: with `HYPOTHESIS_SELF_STUDY_MERGE=true`, `run_self_study()` adds `hypothesis_calibration` to `.self_study.json`
4. **Gate**: with `HYPOTHESIS_PROMOTION_GUARD_ENABLED=true`, advisory model promotion vetoed if hit rate < 0.45

## Related Pages

- [[advisory-model]] — calibration data source
- [[signal-scanner]] — source of predictions
- [[self-study]] — complementary analysis
- [[feature-flags]] — hypothesis env vars

---

*Last compiled: 2026-04-13*
