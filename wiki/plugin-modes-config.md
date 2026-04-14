---
source: Brain/Config/Plugin Modes Config.md
created: 2026-04-13
updated: 2026-04-13
tags: [config, plugins]
---

# Plugin Modes Config

> All environment variables for the plugin mode system.

## Execution Quality

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXEC_QUALITY_MODE` | off | off / shadow / live |
| `EXEC_SPREAD_MAX_BPS` | 35 | Max spread in basis points |
| `EXEC_SLIPPAGE_MAX_BPS` | 20 | Max slippage in bps |
| `EXEC_REPRICE_ATTEMPTS` | 2 | Cancel/replace attempts |
| `EXEC_USE_LIMIT_FOR_LIQUID` | true | Prefer limit orders for liquid names |

## Exit Manager

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXIT_MANAGER_MODE` | off | off / shadow / live |
| `EXIT_PARTIAL_TP_R_MULT` | 1.5 | R-multiple for first partial TP |
| `EXIT_PARTIAL_TP_FRACTION` | 0.5 | Fraction to trim |
| `EXIT_BREAKEVEN_AFTER_PARTIAL` | true | Move stop to breakeven after partial |
| `EXIT_MAX_HOLD_DAYS` | 12 | Max hold days before time-stop |

## Event Risk

| Env Var | Default | Description |
|---------|---------|-------------|
| `EVENT_RISK_MODE` | off | off / shadow / live |
| `EVENT_BLOCK_EARNINGS_DAYS` | 2 | Flag symbols near earnings |
| `EVENT_ACTION` | block | `block` or `downsize` |
| `EVENT_DOWNSIZE_FACTOR` | 0.5 | Position multiplier for downsize |

## Regime v2

| Env Var | Default | Description |
|---------|---------|-------------|
| `REGIME_V2_MODE` | off | off / shadow / live |
| `REGIME_V2_ENTRY_MIN_SCORE` | 55 | Minimum regime score for entries |
| `REGIME_V2_SIZE_MULT_HIGH` | 1.0 | Size multiplier: high regime |
| `REGIME_V2_SIZE_MULT_MED` | 0.7 | Size multiplier: medium regime |
| `REGIME_V2_SIZE_MULT_LOW` | 0.4 | Size multiplier: low regime |

## Correlation Guard

| Env Var | Default | Description |
|---------|---------|-------------|
| `CORRELATION_GUARD_MODE` | off | off / shadow / live |
| `CORRELATION_GUARD_MAX_PAIR_CORR` | 0.85 | Max pairwise correlation |

## Strategy Ensemble

| Env Var | Default | Description |
|---------|---------|-------------|
| `STRATEGY_ENSEMBLE_MODE` | shadow | Final ensemble ranking mode |
| `STRATEGY_WEIGHT_BREAKOUT_HIGH` | 1.00 | Breakout weight: high regime |
| `STRATEGY_WEIGHT_PULLBACK_MED` | 1.05 | Pullback weight: medium regime |

## Related Pages

- [[plugin-modes]] — behavior descriptions and rollout sequence
- [[execution-engine]] — where plugins hook in
- [[canary-rollout]] — canary process for promotion

---

*Last compiled: 2026-04-13*
