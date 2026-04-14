---
source: Brain/Strategies/Adaptive Stops.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, risk, stops]
---

# Adaptive Stops

> ATR-based trailing stop sizing that adjusts to each stock's volatility.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADAPTIVE_STOP_ENABLED` | true | Enable adaptive sizing |
| `ADAPTIVE_STOP_BASE_PCT` | 7% | Fallback when ATR unavailable |
| `ADAPTIVE_STOP_MIN_PCT` | 5% | Minimum stop clamp |
| `ADAPTIVE_STOP_MAX_PCT` | 12% | Maximum stop clamp |
| `ADAPTIVE_STOP_ATR_MULT` | 2.5 | ATR multiplier for stop distance |
| `STOP_ORDER_DURATION` | `GOOD_TILL_CANCEL` | Stop order time-in-force |

## Related Pages

- [[guardrails]] — stops attached after BUY
- [[execution-engine]] — stop order placement

---

*Last compiled: 2026-04-13*
