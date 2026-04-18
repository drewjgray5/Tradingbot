---
source: schwab_skill/backtest.py
created: 2026-04-17
updated: 2026-04-17
tags: [backtest, validation, scanner]
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
