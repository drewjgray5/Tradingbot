---
source: Brain/Strategies/PEAD.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, earnings]
---

# PEAD

> Post-Earnings Announcement Drift — boosts or penalizes signals based on recent earnings surprises.

## Rationale

Academic research shows stocks drift in the direction of their earnings surprise for weeks after announcement.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `PEAD_ENABLED` | true | Enable PEAD enrichment |
| `PEAD_LOOKBACK_DAYS` | 10 | Recent earnings window |
| `PEAD_SCORE_BOOST` | 3.0 | Boost for positive surprise |
| `PEAD_SCORE_BOOST_LARGE` | 5.0 | Boost for strong positive surprise |
| `PEAD_SCORE_PENALTY` | 3.0 | Penalty for negative surprise |

## Related Pages

- [[signal-ranking]] — PEAD adjustments modify composite score
- [[signal-scanner]] — applied during Stage B enrichment

---

*Last compiled: 2026-04-13*
