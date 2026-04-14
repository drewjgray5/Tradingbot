---
source: Brain/Strategies/Plugin Modes.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, plugins, risk]
---

# Plugin Modes

> All new plugins follow OFF → SHADOW → LIVE rollout.

## Mode Definitions

| Mode | Behavior |
|------|----------|
| `off` | Legacy behavior, plugin disabled |
| `shadow` | Compute + diagnostics only, no behavior changes |
| `live` | Enforce gates, resize positions, block/allow actions |

## Active Plugins

1. **Execution Quality** (`EXEC_QUALITY_MODE`) — spread/slippage checks
2. **Exit Manager** (`EXIT_MANAGER_MODE`) — partial TP, breakeven, time stops
3. **Event Risk** (`EVENT_RISK_MODE`) — earnings/macro blackouts
4. **Regime v2** (`REGIME_V2_MODE`) — score-based sizing
5. **Correlation Guard** (`CORRELATION_GUARD_MODE`) — pairwise limits

## Recommended Rollout Sequence

1. `EXEC_QUALITY_MODE` shadow → live
2. `EVENT_RISK_MODE` shadow → live
3. `REGIME_V2_MODE` shadow → live
4. `EXIT_MANAGER_MODE` shadow → live
5. `CORRELATION_GUARD_MODE` after live-testing

**Rule**: Promote one at a time. Hold for at least one full market week.

## Related Pages

- [[execution-engine]] — plugins hook into execution flow
- [[canary-rollout]] — canary process
- [[plugin-modes-config]] — full env var reference

---

*Last compiled: 2026-04-13*
