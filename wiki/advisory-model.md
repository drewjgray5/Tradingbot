---
source: Brain/Architecture/Advisory Model.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, model, calibration]
---

# Advisory Model

> Calibrated P(up in 10 days) probability scoring overlaid on scanner signals.

## How It Works

- Trained on historical signal outcomes
- Produces a probability estimate for each signal
- Confidence bands: HIGH (>= 0.62), MEDIUM (>= 0.52), LOW (< 0.52)
- Model artifact at `ADVISORY_MODEL_PATH` (default `artifacts/advisory_model_v1.json`)

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADVISORY_MODEL_ENABLED` | true | Enable advisory scoring |
| `ADVISORY_MODEL_PATH` | `artifacts/advisory_model_v1.json` | Model artifact |
| `ADVISORY_CONFIDENCE_HIGH` | 0.62 | High confidence threshold |
| `ADVISORY_CONFIDENCE_LOW` | 0.52 | Medium confidence threshold |
| `ADVISORY_REQUIRE_MODEL` | false | Fail validation if model missing |

## Champion/Challenger

- Champion model serves production scores
- Challenger tested via A/B in [[hypothesis-ledger]]
- Promotion gated on `HYPOTHESIS_PROMOTION_MIN_HIT_RATE` (0.45)
- Scripts: `scripts/decide_and_promote_advisory_model.py`

## Related Pages

- [[signal-scanner]] — advisory scoring in Stage B
- [[signal-ranking]] — P(up) overlays composite score
- [[hypothesis-ledger]] — calibration data source
- [[feature-flags]] — advisory config vars

---

*Last compiled: 2026-04-13*
