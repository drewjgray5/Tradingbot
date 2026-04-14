---
tags: [strategy, risk, stops]
---
# Adaptive Stops

ATR-based trailing stop sizing that adjusts to each stock's volatility and trend regime.

## How It Works
Instead of a fixed percentage stop, adaptive stops use the Average True Range (ATR) to set stop distance proportional to the stock's actual volatility.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADAPTIVE_STOP_ENABLED` | true | Enable adaptive sizing |
| `ADAPTIVE_STOP_BASE_PCT` | 0.07 (7%) | Fallback when ATR unavailable |
| `ADAPTIVE_STOP_MIN_PCT` | 0.05 (5%) | Minimum stop percent clamp |
| `ADAPTIVE_STOP_MAX_PCT` | 0.12 (12%) | Maximum stop percent clamp |
| `ADAPTIVE_STOP_ATR_MULT` | 2.5 | ATR multiplier for stop distance |
| `ADAPTIVE_STOP_TREND_LOOKBACK` | 20 | Lookback for trend regime adjustment |
| `STOP_ORDER_DURATION` | `GOOD_TILL_CANCEL` | `DAY` or `GOOD_TILL_CANCEL` |

## Related
- [[Guardrails]] — stops attached after successful BUY
- [[Execution Engine]] — stop order placement
