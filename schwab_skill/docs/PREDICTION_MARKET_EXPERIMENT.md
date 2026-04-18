# Prediction-Market A/B Experiment

This experiment is evaluation-only. It does not modify production LIVE behavior.

## Objective

Compare:

- **Control (A):** prediction-market overlay disabled
- **Treatment (B):** prediction-market overlay enabled

for both:

- historical backtest outcomes
- current-session shadow scan differences

## Strict Data Constraints

- **No lookahead:** treatment backtests require historical prediction-market snapshots file replayed point-in-time (`updated_ts <= entry timestamp`).
- **No survivorship bias:** require a frozen universe file with an `as_of` date on or before backtest start.

## Required Inputs

### 1) Frozen universe file

JSON object:

```json
{
  "as_of": "2025-01-01",
  "tickers": ["AAPL", "MSFT", "NVDA"]
}
```

### 2) Historical prediction-market snapshots

JSON array:

```json
[
  {
    "ticker": "AAPL",
    "event_id": "ev_123",
    "event_name": "AAPL up this week",
    "implied_probability": 0.63,
    "updated_ts": "2026-01-03T15:30:00Z",
    "resolution_ts": "2026-01-10T21:00:00Z",
    "liquidity": 12000.0,
    "spread": 0.03,
    "volume": 45000.0
  }
]
```

## Run

```bash
python scripts/evaluate_prediction_market_ab.py \
  --start-date 2025-01-15 \
  --end-date 2026-04-01 \
  --universe-file /path/to/frozen_universe.json \
  --pm-historical-file /path/to/pm_snapshots.json
```

Optional:

- add `--skip-shadow` to run backtest-only.

## Outputs

- `/.prediction_market_ab_results.json` history
- `/.prediction_market_shadow_eval.json` history

Each A/B record includes:

- paired-trade delta summary
- bootstrap 95% CI on mean delta
- counts for overlap/control-only/treatment-only trades
- verdict: `treatment_better`, `control_better`, or `inconclusive`
