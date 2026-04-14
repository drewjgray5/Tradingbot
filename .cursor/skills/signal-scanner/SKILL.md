---
name: signal-scanner
description: >-
  Signal scanning pipeline including Stage 2 analysis, VCP detection, sector
  strength, PEAD, forensic accounting, advisory model, quality gates, and
  strategy ensemble. Use when working on signal_scanner.py, stage_analysis.py,
  sector_strength.py, scan logic, signal ranking, quality gates, diagnostics,
  or any trading strategy code.
---

# Signal Scanner

## Two-Stage Pipeline

### Stage A — Fast Structural Filters (Parallel)
Workers: `SCAN_STAGE_A_MAX_WORKERS` (default 4), timeout: `SCAN_STAGE_TASK_TIMEOUT_SEC` (120s)

1. **Stage 2 Analysis**: price within `STAGE2_52W_PCT` (85%) of 52W high + 200 SMA rising for `STAGE2_SMA_UPWARD_DAYS` (20) days
2. **VCP Detection**: `VCP_DAYS` (4) consecutive days with volume below 50-day average
3. **Sector filter** (optional): `SECTOR_FILTER_ENABLED` — stock's sector must outperform SPY

Output: shortlist of `SIGNAL_TOP_N * SCAN_STAGE_A_SHORTLIST_MULTIPLIER` tickers, capped at `SCAN_STAGE_A_SHORTLIST_CAP` (40).

### Stage B — Enrichment (Parallel)
Workers: `SCAN_STAGE_B_MAX_WORKERS` (default 4)

1. **PEAD**: boost/penalty based on recent earnings surprise (`PEAD_SCORE_BOOST`, `PEAD_SCORE_PENALTY`)
2. **Forensic accounting**: Sloan ratio, Beneish M-Score, Altman Z-Score checks (`FORENSIC_FILTER_MODE`)
3. **SEC enrichment**: filing analysis, score hints (`SEC_ENRICHMENT_ENABLED`)
4. **Advisory model**: calibrated P(up in 10 days) overlay (`ADVISORY_MODEL_ENABLED`)
5. **Quality gates**: filter weak signals (`QUALITY_GATES_MODE`: off/shadow/soft/hard)
6. **Strategy ensemble**: breakout + pullback weighted by regime (`STRATEGY_ENSEMBLE_MODE`)
7. **Breakout confirmation**: optional intraday price check (`BREAKOUT_CONFIRM_ENABLED`)

## Regime Gate

Before scanning, checks SPY vs 200 SMA. If SPY is below and `SCAN_ALLOW_BEAR_REGIME=false` (default), scan is blocked entirely. Diagnostics will show `scan_blocked_reason: bear_regime_spy_below_200sma`.

## Diagnostics Dict

Every scan returns `(signals, diagnostics)`. Key diagnostic counters:

```python
diagnostics = {
    "scan_blocked": 0,
    "scan_blocked_reason": None,
    "watchlist_size": 0,
    "df_empty": 0,
    "too_few_candles": 0,
    "stage2_fail": 0,
    "vcp_fail": 0,
    "no_sector_etf": 0,
    "sector_not_winning": 0,
    "breakout_not_confirmed": 0,
    "exceptions": 0,
    "self_study_filtered": 0,
    # ...quality gate and plugin counters
}
```

Always increment relevant counter when rejecting a ticker. This powers the dashboard's "why no signals?" explanation.

## Signal Ranking

Composite score from: Stage 2 proximity, VCP quality, sector RS, PEAD, guidance tone, SEC hints, advisory P(up). Sorted descending, top `SIGNAL_TOP_N` (default 5, 0=unlimited) returned.

Each signal carries `strategy_attribution.top_live` labeling the dominant strategy.

## Quality Gates

| Mode | Behavior |
|------|----------|
| `off` | Disabled |
| `shadow` | Track would-filter counts only |
| `soft` | Filter when `QUALITY_SOFT_MIN_REASONS` (2) weak reasons exist |
| `hard` | Filter on any single weak reason |

Exception: `weak_breakout_volume` is always hard regardless of mode.

## Plugin Modes (OFF → SHADOW → LIVE)

All plugins follow the same pattern. Current plugins:
- **Execution Quality** (`EXEC_QUALITY_MODE`): spread/slippage checks, limit orders
- **Exit Manager** (`EXIT_MANAGER_MODE`): partial TP, breakeven stops, time stops
- **Event Risk** (`EVENT_RISK_MODE`): earnings/macro blackout windows
- **Regime v2** (`REGIME_V2_MODE`): score-based sizing multipliers
- **Correlation Guard** (`CORRELATION_GUARD_MODE`): pairwise correlation limits
- **Strategy Ensemble** (`STRATEGY_ENSEMBLE_MODE`): regime-weighted strategy blending

Promote one at a time. Hold shadow for 3-5 sessions minimum. See `Brain/Runbooks/Canary Rollout.md`.

## Key Files

- `schwab_skill/signal_scanner.py` — main pipeline, `scan_for_signals_detailed()`
- `schwab_skill/stage_analysis.py` — `is_stage_2()`, `check_vcp_volume()`, `add_indicators()`
- `schwab_skill/sector_strength.py` — `get_sector_heatmap()`
- `schwab_skill/advisory_model.py` — calibrated probability scoring
- `schwab_skill/config.py` — all env var getters with defaults
