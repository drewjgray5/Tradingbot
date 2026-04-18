---
source: Brain/Architecture/Signal Scanner.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, scanner, signals]
---

# Signal Scanner

> Two-stage parallel scan pipeline: Stage A (cheap structural filters) → Stage B (enrichment and ranking).

## Stage A — Fast Filters

Workers: `SCAN_STAGE_A_MAX_WORKERS` (default 4)

1. [[stage-2-analysis]] — price within 85% of 52W high + rising 200 SMA
2. [[vcp-detection]] — consecutive days of volume contraction
3. Sector filter (optional) — stock's sector outperforms SPY

Output: shortlist sized by `SIGNAL_TOP_N * SCAN_STAGE_A_SHORTLIST_MULTIPLIER`, capped at 40.

## Stage B — Enrichment

Workers: `SCAN_STAGE_B_MAX_WORKERS` (default 4)

1. [[pead]] — post-earnings drift scoring
2. [[forensic-accounting]] — Sloan, Beneish, Altman checks
3. SEC enrichment — filing analysis and score hints
4. [[advisory-model]] — calibrated P(up) overlay
5. [[mirofish-engine]] — multi-persona votes aggregated into `mirofish_conviction`
6. [[prediction-market]] — Polymarket overlay (shadow/live; gated by liquidity + spread)
7. [[quality-gates]] — weak signal filtering
8. Strategy ensemble — breakout + pullback weighted by regime
9. Breakout confirmation — optional intraday price check
10. [[meta-policy]] — final emit / suppress / downweight (via [[agent-intelligence]])

## Regime Gate

SPY must be above 200 SMA unless `SCAN_ALLOW_BEAR_REGIME=true`. Blocked scans report `scan_blocked_reason: bear_regime_spy_below_200sma` in diagnostics.

## Diagnostics

Every scan returns `(signals, diagnostics)` with counters: `stage2_fail`, `vcp_fail`, `sector_not_winning`, `breakout_not_confirmed`, `exceptions`, etc. Powers the dashboard's "why no signals?" UI.

## Key Functions

- `scan_for_signals_detailed()` — main entry point
- `get_signal_quality_summary()` — aggregates diagnostics across stored scans

## Related Pages

- [[signal-ranking]] — how signals are scored and sorted
- [[scanner-tunables]] — all env var knobs
- [[quality-gates]] — quality filtering modes
- [[system-overview]] — pipeline context
- [[agent-intelligence]] — dynamic weighting + meta-policy applied to scanner output
- [[mirofish-engine]] — Stage B persona simulation
- [[prediction-market]] — Stage B prediction-market overlay
- [[backtest]] — historical replay that reuses these same Stage A/B rules
- [[feature-store]] — every emitted signal is recorded here

---

*Last compiled: 2026-04-13*
