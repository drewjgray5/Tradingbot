---
source: schwab_skill/backtest.py
created: 2026-04-17
updated: 2026-04-18
tags: [backtest, validation, scanner, portfolio-sim]
---

# Backtest Harness

> The historical replay loop that validates strategy changes before they
> reach live trading. Shares its scanner rules with the live pipeline so
> backtest PnL stays a faithful proxy for live behaviour.

## What it does

`schwab_skill/backtest.py` walks a watchlist over a configurable date
range, replays the same Stage 2 / VCP / quality-gate logic the live
[[signal-scanner]] uses, and produces a per-trade ledger with
return/risk diagnostics. Output lands in `.backtest_results.json`
(skill root) and is consumed by validation scripts.

## Live-parity guarantees

The backtest deliberately reuses several pieces from the live engine
rather than rewriting them — this is the *only* way to make backtest
results predictive of live PnL:

- **Stage 2 / VCP checks** come from `stage_analysis` (same module
  Stage A uses live).
- **Quality gates** come from `signal_scanner._evaluate_quality_gates`
  (same code path that filters live signals).
- **Sector climate filter** uses the same SPY > 200 SMA gate.
- **Adaptive stops** come from `config.get_adaptive_stop_*` so live and
  backtest exit at the same multiples.
- **Plugin overlays** (event risk, exec quality, meta-policy) are
  applied via [[backtest-intelligence-overlay]] so shadow-mode plugins
  can be A/B'd historically before promotion.

## Data sources

- **Schwab** (primary, when `SCHWAB_ONLY_DATA=true`).
- **yfinance** fallback for ad-hoc local runs (3 retries with backoff
  on rate-limits).
- **Polygon** is reachable through the same retry path used live.

Bars are normalised to ``open/high/low/close/volume`` lower-case
columns and de-duplicated before any indicator computation.

## Costs model

Defaults that mirror the live execution profile:

| Constant | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_SLIPPAGE_BPS_PER_SIDE` | 15 bps | Each side of a round trip |
| `DEFAULT_FEE_PER_SHARE` | $0.005 | Per-share commission |
| `DEFAULT_MIN_FEE_PER_ORDER` | $1.00 | Floor per order |
| `DEFAULT_MAX_ADV_PARTICIPATION` | 2% | Liquidity cap |

Override via env vars (see [[scanner-tunables]]).

## Portfolio equity simulator

Per-trade `net_return` from `_net_return_after_costs` is a per-share %
return normalised to ~$10K target notional. The legacy aggregator
chained those returns as `(1+r).cumprod()`, which is mathematically
equivalent to "100% of equity into trade 1, then 100% of resulting
equity into trade 2, ..." and produced fictional -94% to -99%
drawdowns on the Schwab universe.

`backtest._simulate_portfolio_equity` replays the trade list through a
shared equity book with a hard concurrency cap and risk-based (or
fixed-%) sizing. Profit factor is **unchanged** (it is sizing-invariant
under equal-weight); `total_return_net_pct`, `max_drawdown_net_pct`,
and `cagr_net_pct` are now real, deployable numbers.

| Env var | Default | Purpose |
| --- | --- | --- |
| `BACKTEST_PORTFOLIO_ENABLED` | `true` | Master switch. `false` = legacy aggregator (only for repro). |
| `BACKTEST_PORTFOLIO_STARTING_EQUITY` | `100000` | Notional starting capital. |
| `BACKTEST_PORTFOLIO_MAX_POSITIONS` | `10` | Concurrent-position cap. Trades arriving when the book is full are dropped and counted under `portfolio_capacity_filtered`. |
| `BACKTEST_POSITION_SIZE_PCT` | `0.05` | Fallback fixed allocation per trade (used when no stop distance available). |
| `BACKTEST_RISK_PER_TRADE_PCT` | `0.0075` | Per-trade equity risk for stop-distance sizing (Minervini/O'Neil convention). Set `0` to force fixed-%. |

Each backtest result now exposes a `portfolio_summary` block with
`avg_concurrent`, `peak_concurrent`, `capacity_filtered`,
`risk_sized_count`, `fixed_sized_count`, and `ending_equity`.

The Schwab-only multi-era runner
(`scripts/run_multi_era_backtest_schwab_only.py`) consumes the same
helper inside `_aggregate_era`. Chunks now persist `entry_date` and
`stop_pct` so the simulator can compute accurate concurrency and
risk-based sizing across the full universe; pre-patch chunks fall back
to instantaneous closes and fixed-% sizing.

See `validation_artifacts/phase0_sizing_audit_*.md` for the
before/after comparison that confirmed the legacy DDs were aggregator
artifacts, not strategy risk.

## Validation entry points

- `scripts/validate_backtest.py` — small ticker count, used by
  `validate_all --profile ci`.
- `scripts/validate_pf_robustness.py` — multi-window, multi-universe
  PF regression test feeding [[promotion-playbook]].
- `scripts/run_multi_era_backtest_schwab_only.py` — long-horizon era
  sweep, chunked + crash-resumable.

## Related Pages

- [[signal-scanner]] — Live pipeline whose rules the backtest replays
- [[backtest-intelligence-overlay]] — Plugin-aware historical attribution
- [[validation]] — Where backtest fits into the broader gate matrix
- [[promotion-playbook]] — Promotion gates that consume backtest PF

---

*Last compiled: 2026-04-17*
