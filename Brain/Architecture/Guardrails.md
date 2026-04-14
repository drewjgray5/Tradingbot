---
tags: [architecture, risk]
---
# Guardrails

Risk controls that prevent the execution engine from exceeding defined limits.

## Hard Limits

| Guardrail | Env Var | Default |
|-----------|---------|---------|
| Max total account value | `MAX_TOTAL_ACCOUNT_VALUE` | $500,000 |
| Max per-ticker position | `MAX_POSITION_PER_TICKER` | $50,000 |
| Max trades per day | `MAX_TRADES_PER_DAY` | 20 |
| Max sector fraction | `MAX_SECTOR_ACCOUNT_FRACTION` | 0 (disabled) |

## Behavior on Block
- Returns an error string to the caller
- Sends a Discord guardrail warning via `notifier.py`
- Trade is **not** submitted to Schwab

## Trailing Stops
Every successful BUY order automatically attaches a trailing stop:
- Default stop: 7% trailing
- `ADAPTIVE_STOP_ENABLED` (default true) — uses ATR + trend regime for dynamic sizing
- Clamped between `ADAPTIVE_STOP_MIN_PCT` (5%) and `ADAPTIVE_STOP_MAX_PCT` (12%)
- ATR multiplier: `ADAPTIVE_STOP_ATR_MULT` (default 2.5x)
- Duration: `STOP_ORDER_DURATION` — `DAY` or `GOOD_TILL_CANCEL` (default GTC)

## Volatility Position Sizing
Optional alternative to fixed `POSITION_SIZE_USD`:
- `VOLATILITY_SIZING_ENABLED` (default false)
- `VOLATILITY_BASE_USD` — base USD when ATR_mult=1.0 (default $5,000)
- `VOLATILITY_ATR_MULT` — target ATR multiple for stop distance (default 2.0)

## Circuit Breaker
Repeated connection failures open the Schwab circuit breaker, blocking all API calls until reset. Recovery hint shown in `/api/health/deep` > `quote_health`.

## Related
- [[Execution Engine]] — applies these guardrails
- [[Plugin Modes]] — additional risk gates (event risk, regime v2, correlation)
- [[Scanner Tunables]] — scanner-side regime gate (`SCAN_ALLOW_BEAR_REGIME`)
