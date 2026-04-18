---
source: Brain/Config/Scanner Tunables.md
created: 2026-04-13
updated: 2026-04-13
tags: [config, scanner, tunables]
---

# Scanner Tunables

> All environment variables that control the signal scanning pipeline.

## Stage 2 Analysis

| Env Var | Default | Description |
|---------|---------|-------------|
| `STAGE2_52W_PCT` | 0.85 | Price must be within this fraction of 52W high |
| `STAGE2_SMA_UPWARD_DAYS` | 20 | 200 SMA must be rising for N days |

## VCP Detection

| Env Var | Default | Description |
|---------|---------|-------------|
| `VCP_DAYS` | 4 | Consecutive days volume below 50-day avg |

## Breakout Confirmation

| Env Var | Default | Description |
|---------|---------|-------------|
| `BREAKOUT_CONFIRM_ENABLED` | true | Require intraday price above prior high |

## Signal Selection

| Env Var | Default | Description |
|---------|---------|-------------|
| `SIGNAL_TOP_N` | 5 | Max signals to return (0 = unlimited) |
| `SECTOR_FILTER_ENABLED` | true | Filter by sector outperformance |

## Worker Parallelism

| Env Var | Default | Description |
|---------|---------|-------------|
| `SCAN_STAGE_A_MAX_WORKERS` | 4 | Stage A concurrent threads |
| `SCAN_STAGE_B_MAX_WORKERS` | 4 | Stage B concurrent threads |
| `SCAN_STAGE_TASK_TIMEOUT_SEC` | 120 | Per-ticker timeout (seconds) |

## Shortlist Sizing

| Env Var | Default | Description |
|---------|---------|-------------|
| `SCAN_STAGE_A_SHORTLIST_MULTIPLIER` | 3.0 | Shortlist width relative to `SIGNAL_TOP_N` |
| `SCAN_STAGE_A_SHORTLIST_CAP` | 40 | Hard cap for Stage A shortlist |

## Regime Gate

| Env Var | Default | Description |
|---------|---------|-------------|
| `SCAN_ALLOW_BEAR_REGIME` | false | Allow scans when SPY below 200 SMA |

## Universe Selection

| Env Var | Default | Description |
|---------|---------|-------------|
| `SIGNAL_UNIVERSE_MODE` | broad | `broad` or `focused` |
| `SIGNAL_SCAN_FULL_UNIVERSE` | true | Skip prefiltering |

## Backtest Portfolio Simulator

Replay-time only. Replaces the legacy `(1+r).cumprod()` chain that
produced fictional drawdowns. See [[backtest]] for full context.

| Env Var | Default | Description |
|---------|---------|-------------|
| `BACKTEST_PORTFOLIO_ENABLED` | true | Master switch; `false` reverts to legacy aggregator (repro only) |
| `BACKTEST_PORTFOLIO_STARTING_EQUITY` | 100000 | Notional starting capital for the equity book |
| `BACKTEST_PORTFOLIO_MAX_POSITIONS` | 10 | Hard cap on simultaneous open positions |
| `BACKTEST_POSITION_SIZE_PCT` | 0.05 | Fallback fixed allocation per trade when no stop distance available |
| `BACKTEST_RISK_PER_TRADE_PCT` | 0.0075 | Per-trade equity risk for stop-distance sizing (0 to disable) |

## Related Pages

- [[signal-scanner]] — how the scanner works
- [[stage-2-analysis]], [[vcp-detection]] — what the thresholds mean
- [[quality-gates]] — quality gate tunables
- [[backtest]] — portfolio simulator + harness behaviour
- [[feature-flags]] — other config domains

---

*Last compiled: 2026-04-18*
