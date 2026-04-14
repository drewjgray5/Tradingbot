---
tags: [architecture, ml, advisory]
---
# Advisory Model

Phase 1 advisory-only probability scoring that enriches scan signals with calibrated `P(up in 10 trading days)`.

## Purpose
Adds a calibrated probability estimate to each scan signal without changing execution behavior. This helps with signal ranking and human decision-making.

## How It Works
1. Scanner produces raw signals with features (signal score, technicals, sector relative strength, SEC risk)
2. Advisory model loads trained artifact (`artifacts/advisory_model_v1.json` by default)
3. Computes `P(up_10d)` for each signal
4. Assigns confidence bucket: **high** (>= 0.62), **medium** (0.52-0.62), **low** (< 0.52)
5. Results appear in signal payloads under `advisory` key

## Features Used
- Signal score (composite technical score)
- Stage 2 and VCP indicators
- Sector relative strength
- SEC risk tags
- `miro_continuation_prob` and `miro_bull_trap_prob` (when MiroFish integration is configured)

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADVISORY_MODEL_ENABLED` | `true` | Enable advisory scoring on scans |
| `ADVISORY_MODEL_PATH` | `artifacts/advisory_model_v1.json` | Model artifact path |
| `ADVISORY_CONFIDENCE_HIGH` | `0.62` | High confidence threshold |
| `ADVISORY_CONFIDENCE_LOW` | `0.52` | Medium confidence threshold |
| `ADVISORY_REQUIRE_MODEL` | `false` | Fail validation if model missing |

## Champion/Challenger Workflow
1. Train challenger: `python scripts/train_and_evaluate_challenger.py --profile promotion`
2. Evaluate: `python scripts/decide_and_promote_advisory_model.py --strict --promotion`
3. Promote (with manual approval): `MANUAL_PROMOTION_APPROVED=1 python scripts/decide_and_promote_advisory_model.py --strict --promotion --apply --notify`

## Key File
`schwab_skill/advisory_model.py`

## Spec
Full specification: `schwab_skill/ADVISORY_MODEL_SPEC.md`

## Related
- [[Signal Scanner]] — advisory overlay happens after ranking
- [[Hypothesis Ledger]] — tracks decision quality for advisory predictions
- [[Signal Ranking]] — advisory confidence feeds into final ranking
