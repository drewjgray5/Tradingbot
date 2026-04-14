---
source: Brain/Strategies/Self-Study.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, learning]
---

# Self-Study

> Automated analysis of trade outcomes to learn from real results.

## How It Works

1. Records filled BUY/SELL prices, computes round-trip returns
2. Analyzes performance by conviction band and sector
3. With 5+ round trips, suggests minimum conviction threshold
4. When `SELF_STUDY_ENABLED=true`, signals below threshold are filtered

## Schedule

Runs at 4:00 PM ET via `main.py` scheduler. Manual: `python scripts/run_self_study.py`.

## Key Files

- `schwab_skill/self_study.py` — analysis logic
- `.self_study.json` — persisted results
- `.trade_outcomes.json` — raw trade data

## Related Pages

- [[hypothesis-ledger]] — complementary decision quality tracking
- [[advisory-model]] — calibration data
- [[system-overview]] — learning loop

---

*Last compiled: 2026-04-13*
