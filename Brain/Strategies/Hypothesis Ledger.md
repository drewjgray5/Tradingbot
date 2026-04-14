---
tags: [strategy, calibration, learning]
---
# Hypothesis Ledger

Tracks **decision quality** for scanner signals separately from P&L, enabling calibration of the system's predictive accuracy.

## Why Separate from P&L
P&L conflates signal quality with execution quality, position sizing, and timing. The hypothesis ledger isolates the core question: "When the system said UP, did it actually go up?"

## How It Works

1. **Record**: When `HYPOTHESIS_LEDGER_ENABLED=true`, every signal that triggers a Discord alert appends a row to `.hypothesis_ledger.json` with:
   - Ticker, source, model ID, input fingerprint
   - Structured prediction (direction, reference price, levels)

2. **Score**: Run `python scripts/score_hypothesis_outcomes.py` after enough calendar time. Checks actual price movement at configurable horizons (default T+1, T+5, T+20 trading days).

3. **Review**: With `HYPOTHESIS_SELF_STUDY_MERGE=true`, `run_self_study()` adds `hypothesis_calibration` (hit rates by source) into `.self_study.json`.

4. **Gate promotions**: With `HYPOTHESIS_PROMOTION_GUARD_ENABLED=true`, advisory model promotion scripts veto promotion when combined signal_scanner + advisory hit rates fall below `HYPOTHESIS_PROMOTION_MIN_HIT_RATE`.

## Full Reports
`python full_report.py TICKER --record-hypothesis` appends a hypothesis for the full-report conclusion when the ledger is enabled.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `HYPOTHESIS_LEDGER_ENABLED` | `false` | Enable recording |
| `HYPOTHESIS_SCORE_HORIZONS` | `1,5,20` | Trading-day horizons for scoring |
| `HYPOTHESIS_SELF_STUDY_MERGE` | `false` | Include in self-study output |
| `HYPOTHESIS_PROMOTION_GUARD_ENABLED` | `false` | Gate promotions on hit rate |
| `HYPOTHESIS_PROMOTION_MIN_N` | 30 | Minimum scored outcomes required |
| `HYPOTHESIS_PROMOTION_MIN_HIT_RATE` | 0.45 | Minimum combined hit rate |

## Key Files
- `.hypothesis_ledger.json` — recorded predictions
- `scripts/score_hypothesis_outcomes.py` — outcome scorer
- `scripts/decide_and_promote_advisory_model.py` — checks promotion gate

## Related
- [[Advisory Model]] — calibrates using hypothesis data
- [[Signal Scanner]] — source of signal predictions
- [[Feature Flags]] — hypothesis-related env vars
