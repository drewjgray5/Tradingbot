---
source: schwab_skill/prediction_market.py, schwab_skill/docs/PREDICTION_MARKET_ROLLOUT.md
created: 2026-04-17
updated: 2026-04-17
tags: [scanner, prediction-market, polymarket, overlay]
---

# Prediction Market Overlay

> Stage B context overlay that lifts or dampens signals based on
> prediction-market consensus probabilities.

## Architecture

The provider abstraction (`PredictionMarketProvider` protocol) decouples the
scanner from any single data source. Three providers ship today:

- `PolymarketProvider` — live Polymarket order book snapshots.
- `StubProvider` — deterministic test fixture for unit tests.
- `HistoricalSnapshotProvider` — replays a frozen JSON store for backtests
  (built by `scripts/build_pm_snapshot_store.py`).

Snapshots carry: `probability`, `confidence`, `liquidity_usd`, `spread`,
`source`, and `as_of`. They flow through the scanner as a Stage B enrichment
field (`signal["prediction_market"]`).

## Safety controls

- **Timestamp safety** — snapshots older than `PRED_MARKET_MAX_AGE_MIN` are
  rejected to avoid stale prices leaking into live decisions.
- **Liquidity gate** — markets thinner than `PRED_MARKET_MIN_LIQUIDITY_USD`
  are ignored.
- **Spread gate** — markets wider than `PRED_MARKET_MAX_SPREAD` are ignored.
- **Impact clamp** — final score adjustment is clamped to
  `PRED_MARKET_MAX_BOOST` / `-PRED_MARKET_MAX_PENALTY` so a single market
  cannot dominate ranking.

## Modes

`PRED_MARKET_MODE` follows the [[plugin-modes]] convention:

- `off` — provider is not even instantiated.
- `shadow` — snapshots are fetched and recorded into
  `signal["prediction_market"]` for diagnostics, but `signal_score` is not
  modified.
- `live` — score adjustments are applied to ranking and quality gates.

## A/B experiment

`scripts/evaluate_prediction_market_ab.py` runs the same scan twice (control =
disabled, treatment = enabled) over a frozen universe and emits
`.prediction_market_ab_results.json`. See
[[../schwab_skill/docs/PREDICTION_MARKET_EXPERIMENT|PREDICTION_MARKET_EXPERIMENT.md]]
for the full procedure.

## Related Pages

- [[signal-scanner]] — Stage B overlay integration point
- [[agent-intelligence]] — Meta-policy consumes PM confidence
- [[promotion-playbook]] — Promotion gates for `PRED_MARKET_MODE=live`

---
*Last compiled: 2026-04-17*
