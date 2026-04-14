---
tags: [strategy, plugins, risk]
---
# Plugin Modes

All new risk and execution plugins follow the same rollout pattern: **OFF -> SHADOW -> LIVE**.

## Mode Definitions

| Mode | Behavior |
|------|----------|
| `off` | Legacy behavior preserved, plugin disabled |
| `shadow` | Compute + diagnostics only, no behavior changes |
| `live` | Enforce gates, resize positions, block/allow actions |

## Active Plugins

### Execution Quality (`EXEC_QUALITY_MODE`)
Checks bid/ask spread and expected slippage before order submission.

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXEC_QUALITY_MODE` | `off` | Plugin mode |
| `EXEC_SPREAD_MAX_BPS` | 35 | Max allowed spread in basis points |
| `EXEC_SLIPPAGE_MAX_BPS` | 20 | Max expected slippage in bps |
| `EXEC_REPRICE_ATTEMPTS` | 2 | Cancel/replace attempts for limits |
| `EXEC_REPRICE_INTERVAL_SEC` | 3 | Seconds between reprices |
| `EXEC_USE_LIMIT_FOR_LIQUID` | true | Prefer limit orders for liquid names |

### Exit Manager (`EXIT_MANAGER_MODE`)
Manages position exits: partial take-profit, breakeven stops, time stops.

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXIT_MANAGER_MODE` | `off` | Plugin mode |
| `EXIT_PARTIAL_TP_R_MULT` | 1.5 | R-multiple for first partial TP |
| `EXIT_PARTIAL_TP_FRACTION` | 0.5 | Fraction to trim at partial TP |
| `EXIT_BREAKEVEN_AFTER_PARTIAL` | true | Move stop to breakeven after partial |
| `EXIT_MAX_HOLD_DAYS` | 12 | Max hold days before time-stop |

### Event Risk (`EVENT_RISK_MODE`)
Blocks or downsizes trades near earnings or macro events.

| Env Var | Default | Description |
|---------|---------|-------------|
| `EVENT_RISK_MODE` | `off` | Plugin mode |
| `EVENT_BLOCK_EARNINGS_DAYS` | 2 | Flag symbols with earnings within +/- N days |
| `EVENT_MACRO_BLACKOUT_ENABLED` | false | Check macro blackout dates |
| `EVENT_ACTION` | `block` | `block` or `downsize` |
| `EVENT_DOWNSIZE_FACTOR` | 0.5 | Position multiplier for downsize |

### Regime v2 (`REGIME_V2_MODE`)
Score-based market regime assessment affecting entry gates and position sizing.

| Env Var | Default | Description |
|---------|---------|-------------|
| `REGIME_V2_MODE` | `off` | Plugin mode |
| `REGIME_V2_ENTRY_MIN_SCORE` | 55 | Minimum regime score for entries |
| `REGIME_V2_SIZE_MULT_HIGH` | 1.0 | Size multiplier: high regime |
| `REGIME_V2_SIZE_MULT_MED` | 0.7 | Size multiplier: medium regime |
| `REGIME_V2_SIZE_MULT_LOW` | 0.4 | Size multiplier: low regime |

### Correlation Guard (`CORRELATION_GUARD_MODE`)
Limits correlated position exposure.

| Env Var | Default | Description |
|---------|---------|-------------|
| `CORRELATION_GUARD_MODE` | `off` | Plugin mode |
| `CORRELATION_GUARD_MAX_PAIR_CORR` | 0.85 | Max pairwise correlation |

## Recommended Rollout Sequence

1. `EXEC_QUALITY_MODE=shadow` -> `live`
2. `EVENT_RISK_MODE=shadow` -> `live`
3. `REGIME_V2_MODE=shadow` -> `live`
4. `EXIT_MANAGER_MODE=shadow` -> `live`
5. `CORRELATION_GUARD_MODE` only after implementation is live-tested

**Rule**: Promote one plugin at a time. Hold for at least one full market week before proceeding.

## Related
- [[Execution Engine]] — plugins hook into execution flow
- [[Canary Rollout]] — canary process for plugin promotion
- [[Plugin Modes Config]] — full env var reference
