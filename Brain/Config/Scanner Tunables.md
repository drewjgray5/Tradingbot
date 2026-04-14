---
tags: [config, scanner]
---
# Scanner Tunables

All environment variables that control the signal scanning pipeline.

## Stage 2 Analysis

| Env Var | Default | Description |
|---------|---------|-------------|
| `STAGE2_52W_PCT` | 0.85 | Price must be within this fraction of 52-week high |
| `STAGE2_SMA_UPWARD_DAYS` | 20 | 200 SMA must be upward for N days |

## VCP Detection

| Env Var | Default | Description |
|---------|---------|-------------|
| `VCP_DAYS` | 4 | Consecutive days volume below 50-day avg |

## Breakout Confirmation

| Env Var | Default | Description |
|---------|---------|-------------|
| `BREAKOUT_CONFIRM_ENABLED` | true | Require intraday price above prior high |
| `BREAKOUT_CONFIRM_MIN_TIME` | 570 | Minutes from midnight (570 = 9:30 AM) |

## Signal Selection

| Env Var | Default | Description |
|---------|---------|-------------|
| `SIGNAL_TOP_N` | 5 | Max signals to return (0 = no limit) |
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
| `SIGNAL_UNIVERSE_TARGET_SIZE` | 250 | Target size for focused mode |
| `SIGNAL_SCAN_FULL_UNIVERSE` | true | Skip prefiltering on full index list |

## Data Source

| Env Var | Default | Description |
|---------|---------|-------------|
| `PREFER_SCHWAB_DATA` | true | Prefer Schwab over yfinance |

## Related
- [[Signal Scanner]] — how the scanner works
- [[Stage 2 Analysis]], [[VCP Detection]] — what the thresholds mean
- [[Quality Gates]] — quality gate tunables
