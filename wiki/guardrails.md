---
source: Brain/Architecture/Guardrails.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, risk, guardrails]
---

# Guardrails

> Risk limits, circuit breaker, and position sizing constraints that wrap execution.

## Position Sizing

- Adaptive stops set per-trade risk based on ATR (see [[adaptive-stops]])
- Regime v2 multipliers scale size by market conditions
- `MAX_SECTOR_ACCOUNT_FRACTION` caps single-sector exposure

## Circuit Breaker

Trips after consecutive Schwab API failures. Auto-recovers after cooldown. Health check: `GET /api/health/deep` shows circuit breaker status.

## Kill Switches

| Env Var | Scope | Effect |
|---------|-------|--------|
| `LIVE_TRADING_KILL_SWITCH` | Platform-wide | Halt all BUY orders |
| `LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS` | Platform-wide | Also block SELL orders |
| `USER_TRADING_HALTED` | Per-user (SaaS) | Pause individual user trading |

## Data Quality Policy

`DATA_QUALITY_EXEC_POLICY`: `off`, `warn`, or `block_risk_increasing`

Checks quote staleness (`DATA_QUOTE_MAX_AGE_SEC`), bar freshness (`DATA_BAR_MAX_STALENESS_DAYS`), and optional cross-source validation (`DATA_CROSSCHECK_ENABLED`).

## Related Pages

- [[execution-engine]] — guardrails wrap execution
- [[adaptive-stops]] — stop sizing within guardrails
- [[feature-flags]] — kill switches and data quality flags
- [[system-overview]] — pipeline context

---

*Last compiled: 2026-04-13*
