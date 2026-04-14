---
tags: [strategy, ranking]
---
# Signal Ranking

How the scanner scores and ranks signals to surface the top opportunities.

## Scoring Components

The composite signal score is built from multiple factors:

| Factor | Weight/Influence | Source |
|--------|-----------------|--------|
| Stage 2 proximity to 52W high | Core | [[Stage 2 Analysis]] |
| VCP volume contraction quality | Core | [[VCP Detection]] |
| Sector relative strength | Modifier | [[Sector Strength]] |
| PEAD earnings surprise | Boost/penalty | [[PEAD]] |
| Guidance tone | Boost/penalty | `GUIDANCE_SCORE_ENABLED` |
| SEC score hints | Optional modifier | `SEC_SCORE_HINT_ENABLED` |
| Forensic flags | Quality gate | [[Forensic Accounting]] |
| Advisory P(up) | Overlay | [[Advisory Model]] |

## Top-N Selection
- `SIGNAL_TOP_N` (default 5) — maximum signals to return
- 0 = no limit (return all passing signals)
- Signals sorted by composite score descending

## Strategy Attribution
Each signal carries a `strategy_attribution` dict with:
- `top_live` — the dominant strategy label for the signal
- Used by the dashboard to show which strategy drove the signal

## Strategy Ensemble (Plugin)
When `STRATEGY_ENSEMBLE_MODE` is `shadow` or `live`:
- **Breakout** and **Pullback** strategies are weighted separately per regime
- Regime router (`STRATEGY_REGIME_ROUTER_MODE`) adjusts weights based on market conditions
- Weight tunables: `STRATEGY_WEIGHT_BREAKOUT_HIGH`, `STRATEGY_WEIGHT_PULLBACK_MED`, etc.

## Alert Thresholds
- `ALERT_MIN_CONVICTION` (default 20) — below this, no Discord alert sent
- `ALERT_PING_CONVICTION` (default 50) — above this, @ping the user
- `ALERT_PING_SCORE` (default 60) — setup score threshold for @ping

## Related
- [[Signal Scanner]] — ranking happens at the end of Stage B
- [[Advisory Model]] — overlays probability on ranked signals
- [[Scanner Tunables]], [[Feature Flags]]
