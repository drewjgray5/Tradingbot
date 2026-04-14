---
tags: [strategy, learning]
---
# Self-Study

Automated analysis of trade outcomes to learn from real trading results.

## How It Works
1. Records filled BUY/SELL prices and computes round-trip returns
2. Analyzes performance by MiroFish conviction band and sector
3. With 5+ round trips, suggests a minimum conviction threshold
4. When `SELF_STUDY_ENABLED=true`, signals below that threshold are filtered out

## Schedule
- Runs automatically at 4:00 PM ET via `main.py` scheduler
- Manual: `python scripts/run_self_study.py`

## Integration with Hypothesis Ledger
When `HYPOTHESIS_SELF_STUDY_MERGE=true`, self-study output includes `hypothesis_calibration` showing hit rates by source.

## Key Files
- `schwab_skill/self_study.py` — analysis logic
- `.self_study.json` — persisted results
- `.trade_outcomes.json` — raw trade outcome data

## Related
- [[Hypothesis Ledger]] — tracks prediction quality (complementary)
- [[Advisory Model]] — calibration data feeds model evaluation
