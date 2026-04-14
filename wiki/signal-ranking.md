---
source: Brain/Strategies/Signal Ranking.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, ranking]
---

# Signal Ranking

> How the scanner scores and ranks signals to surface top opportunities.

## Scoring Components

| Factor | Source |
|--------|--------|
| Stage 2 proximity to 52W high | [[stage-2-analysis]] |
| VCP volume contraction quality | [[vcp-detection]] |
| Sector relative strength | [[sector-strength]] |
| PEAD earnings surprise | [[pead]] |
| Guidance tone | `GUIDANCE_SCORE_ENABLED` |
| SEC score hints | `SEC_SCORE_HINT_ENABLED` |
| Forensic flags | [[forensic-accounting]] |
| Advisory P(up) | [[advisory-model]] |

## Top-N Selection

`SIGNAL_TOP_N` (default 5, 0 = unlimited). Sorted by composite score descending.

## Strategy Attribution

Each signal carries `strategy_attribution.top_live` — the dominant strategy label.

## Strategy Ensemble

When `STRATEGY_ENSEMBLE_MODE` is shadow/live, breakout and pullback strategies are weighted separately per regime via regime router.

## Related Pages

- [[signal-scanner]] — ranking at end of Stage B
- [[advisory-model]] — probability overlay
- [[scanner-tunables]], [[feature-flags]]

---

*Last compiled: 2026-04-13*
