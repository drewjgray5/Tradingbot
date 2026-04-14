---
tags: [strategy, technical]
---
# Stage 2 Analysis

Based on Stan Weinstein's stage analysis framework. Stage 2 is the markup phase where a stock is in a confirmed uptrend -- the optimal phase for momentum entries.

## What Stage 2 Means

Weinstein divides a stock's lifecycle into four stages:
1. **Stage 1** — Basing (accumulation)
2. **Stage 2** — Advancing (markup) -- **we want to buy here**
3. **Stage 3** — Topping (distribution)
4. **Stage 4** — Declining (markdown)

## Qualification Criteria

The bot uses two quantitative checks:

| Check | Env Var | Default | Logic |
|-------|---------|---------|-------|
| 52-week high proximity | `STAGE2_52W_PCT` | 0.85 | Price must be within 15% of 52-week high |
| 200 SMA upward trend | `STAGE2_SMA_UPWARD_DAYS` | 20 | 200-day SMA must be rising for 20+ consecutive days |

## Implementation
- `is_stage_2(df)` in `stage_analysis.py`
- Uses TA-Lib for SMA computation when available, falls back to pandas
- `add_indicators(df)` adds SMA columns to price DataFrame

## Why These Thresholds
- **85% of 52-week high**: ensures the stock is in an uptrend, not a deep pullback
- **20 days upward SMA**: confirms the trend is sustained, not a short-term bounce
- These are conservative defaults; tighten for higher selectivity, loosen for broader scans

## Tuning
- Raise `STAGE2_52W_PCT` to 0.90+ for stocks closer to highs (more breakout-oriented)
- Lower `STAGE2_SMA_UPWARD_DAYS` to 10 for faster trend detection (more noise)
- Scanner diagnostics show `stage2_fail` count to gauge filter impact

## Related
- [[VCP Detection]] — the next filter after Stage 2 passes
- [[Signal Scanner]] — Stage A uses Stage 2 as first-pass filter
- [[Scanner Tunables]] — env var reference
