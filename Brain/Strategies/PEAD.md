---
tags: [strategy, earnings]
---
# PEAD

Post-Earnings Announcement Drift scoring: boosts or penalizes signals based on recent earnings surprises.

## Rationale
Academic research shows stocks tend to drift in the direction of their earnings surprise for weeks after the announcement. Positive surprises boost momentum; negative surprises are headwinds.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `PEAD_ENABLED` | true | Enable PEAD enrichment |
| `PEAD_LOOKBACK_DAYS` | 10 | Recent earnings window in days |
| `PEAD_SCORE_BOOST` | 3.0 | Score boost for positive surprise |
| `PEAD_SCORE_BOOST_LARGE` | 5.0 | Boost for strong positive surprise |
| `PEAD_SCORE_PENALTY` | 3.0 | Penalty for negative surprise |

## Related
- [[Signal Ranking]] — PEAD adjustments modify composite score
- [[Signal Scanner]] — PEAD applied during Stage B enrichment
