---
source: Brain/Architecture/Execution Engine.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, execution, orders]
---

# Execution Engine

> Order placement with guardrail wrapper, plugin hooks, and adaptive stops.

## Execution Flow

1. Signal passes all quality gates and guardrails
2. Position size computed (adaptive stops, regime multiplier, sector caps)
3. Order submitted via Schwab account session
4. Stop-loss order attached on fill confirmation
5. Execution logged to DB and Discord

## Plugin Hooks

All plugins follow OFF → SHADOW → LIVE rollout:

| Plugin | Env Var | Purpose |
|--------|---------|---------|
| Execution Quality | `EXEC_QUALITY_MODE` | Spread/slippage checks, limit orders |
| Exit Manager | `EXIT_MANAGER_MODE` | Partial TP, breakeven stops, time stops |
| Event Risk | `EVENT_RISK_MODE` | Earnings/macro blackout windows |
| Regime v2 | `REGIME_V2_MODE` | Score-based sizing multipliers |
| Correlation Guard | `CORRELATION_GUARD_MODE` | Pairwise correlation limits |

## Shadow Mode

`EXECUTION_SHADOW_MODE=true` or `PAPER_TRADING_ENABLED=true`: compute everything but don't submit orders. All diagnostics still logged.

## Kill Switch

`LIVE_TRADING_KILL_SWITCH=true` halts all BUY orders. `LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS=true` also blocks SELLs.

## Related Pages

- [[guardrails]] — risk limits wrapped around execution
- [[adaptive-stops]] — ATR-based stop sizing
- [[plugin-modes]] — plugin rollout pattern
- [[system-overview]] — pipeline context

---

*Last compiled: 2026-04-13*
