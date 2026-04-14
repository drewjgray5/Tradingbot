---
tags: [config, plugins]
---
# Plugin Modes Config

All environment variables for the plugin mode system. See [[Plugin Modes]] for behavior descriptions.

## Execution Quality

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXEC_QUALITY_MODE` | off | off / shadow / live |
| `EXEC_QUALITY_MIN_SIGNAL_SCORE` | 55 | Minimum signal score |
| `EXEC_SPREAD_MAX_BPS` | 35 | Max spread in basis points |
| `EXEC_SLIPPAGE_MAX_BPS` | 20 | Max slippage in basis points |
| `EXEC_REPRICE_ATTEMPTS` | 2 | Cancel/replace attempts |
| `EXEC_REPRICE_INTERVAL_SEC` | 3 | Seconds between reprices |
| `EXEC_USE_LIMIT_FOR_LIQUID` | true | Prefer limit orders for liquid names |

## Exit Manager

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXIT_MANAGER_MODE` | off | off / shadow / live |
| `EXIT_MANAGER_TRAIL_ATR_MULT` | 2.0 | Trailing stop ATR multiplier |
| `EXIT_PARTIAL_TP_R_MULT` | 1.5 | R-multiple for first partial take-profit |
| `EXIT_PARTIAL_TP_FRACTION` | 0.5 | Fraction to trim (clamped 0.05-0.95) |
| `EXIT_BREAKEVEN_AFTER_PARTIAL` | true | Move stop to breakeven after partial |
| `EXIT_MAX_HOLD_DAYS` | 12 | Time-stop in trading days |

## Event Risk

| Env Var | Default | Description |
|---------|---------|-------------|
| `EVENT_RISK_MODE` | off | off / shadow / live |
| `EVENT_RISK_BLACKOUT_MINUTES` | 30 | Blackout window around events |
| `EVENT_BLOCK_EARNINGS_DAYS` | 2 | Flag within +/- N days of earnings |
| `EVENT_MACRO_BLACKOUT_ENABLED` | false | Check macro blackout dates |
| `EVENT_ACTION` | block | `block` or `downsize` |
| `EVENT_DOWNSIZE_FACTOR` | 0.5 | Position multiplier for downsize (0.1-1.0) |

## Regime v2

| Env Var | Default | Description |
|---------|---------|-------------|
| `REGIME_V2_MODE` | off | off / shadow / live |
| `REGIME_V2_MIN_CONFIDENCE` | 0.55 | Minimum confidence threshold |
| `REGIME_V2_ENTRY_MIN_SCORE` | 55 | Min regime score for new entries |
| `REGIME_V2_SIZE_MULT_HIGH` | 1.0 | Sizing multiplier: high regime |
| `REGIME_V2_SIZE_MULT_MED` | 0.7 | Sizing multiplier: medium regime |
| `REGIME_V2_SIZE_MULT_LOW` | 0.4 | Sizing multiplier: low regime |

## Correlation Guard

| Env Var | Default | Description |
|---------|---------|-------------|
| `CORRELATION_GUARD_MODE` | off | off / shadow / live |
| `CORRELATION_GUARD_MAX_PAIR_CORR` | 0.85 | Max pairwise correlation allowed |

## Strategy Ensemble

| Env Var | Default | Description |
|---------|---------|-------------|
| `STRATEGY_PULLBACK_MODE` | shadow | Pullback strategy mode |
| `STRATEGY_REGIME_ROUTER_MODE` | shadow | Regime-based weight routing |
| `STRATEGY_ENSEMBLE_MODE` | shadow | Final ensemble ranking mode |

## Strategy Weights

| Env Var | Default | Description |
|---------|---------|-------------|
| `STRATEGY_WEIGHT_BREAKOUT_HIGH` | 1.00 | Breakout weight: high regime |
| `STRATEGY_WEIGHT_BREAKOUT_MED` | 1.00 | Breakout weight: medium regime |
| `STRATEGY_WEIGHT_BREAKOUT_LOW` | 0.95 | Breakout weight: low regime |
| `STRATEGY_WEIGHT_PULLBACK_HIGH` | 0.90 | Pullback weight: high regime |
| `STRATEGY_WEIGHT_PULLBACK_MED` | 1.05 | Pullback weight: medium regime |
| `STRATEGY_WEIGHT_PULLBACK_LOW` | 1.10 | Pullback weight: low regime |

## Related
- [[Plugin Modes]] — behavior descriptions and rollout sequence
- [[Execution Engine]] — where plugins hook in
- [[Canary Rollout]] — canary process for plugin promotion
