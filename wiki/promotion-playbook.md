---
created: 2026-04-17
updated: 2026-04-17
tags: [operations, rollout, governance]
---

# Promotion Playbook

> Repeatable, signed process for moving a feature from `off` → `shadow` → `live`.

## Why this exists

The codebase ships every new intelligence layer behind an
`<NAME>_MODE = off|shadow|live` env var (see [[plugin-modes]]). Without a
documented promotion procedure those flags get stuck in `shadow` because
nobody owns the decision. This page is that owner.

## Lifecycle

```
            ┌────────┐  shadow data sufficient   ┌────────┐  rollback
            │   off  │──────────────────────────▶│ shadow │◀──────────┐
            └────────┘                            └───┬────┘           │
                                                     │                │
                              promotion gates met    │                │
                                                     ▼                │
                                                ┌────────┐            │
                                                │  live  │────────────┘
                                                └────────┘
```

## Promotion gates

A feature may be promoted to `live` only when ALL of the following hold:

1. **Coverage**: ≥ 30 evaluable events across the last 30 days
   (e.g. 30 counterfactual events for `META_POLICY_MODE`).
2. **Effect direction**: the policy must be moving in the intended direction
   (suppressed bucket avg return ≤ fired bucket avg return, etc.).
3. **No regression**: `validate_all.py --baseline validation_artifacts/baseline.json`
   shows no regressed step vs the last known-good baseline.
4. **Hypothesis ledger**: the [[hypothesis-ledger]] entry for the feature is
   scored ≥ +0.3 with at least 10 closed hypotheses.
5. **Operator approval**: a signed entry in
   `schwab_skill/scripts/promotion_ledger.jsonl` (see below).

Coverage and effect numbers come from the per-feature scoring scripts:

| Feature | Scorer |
| --- | --- |
| `META_POLICY_MODE`, `UNCERTAINTY_MODE`, `MIROFISH_WEIGHTING_MODE` | `scripts/score_counterfactual_outcomes.py` |
| `EXEC_QUALITY_MODE` | `scripts/validate_execution_quality.py` |
| `EXIT_MANAGER_MODE` | `scripts/validate_exit_manager.py` |
| `EVENT_RISK_MODE` | `scripts/validate_event_risk.py` |
| `REGIME_V2_MODE` | `scripts/validate_regime_v2.py` |
| `CORRELATION_GUARD_MODE` | `scripts/validate_observability_gates.py` |
| `PRED_MARKET_MODE` | `scripts/evaluate_prediction_market_ab.py` |

## Approval ledger

Replace the legacy `MANUAL_PROMOTION_APPROVED=1` env knob with a
tamper-evident JSONL ledger:

```bash
python scripts/promotion_ledger.py append \
  --target EXEC_QUALITY_MODE=live \
  --reason "Sprint 3 promotion: stop-loss skew within tolerance"

python scripts/promotion_ledger.py verify
python scripts/promotion_ledger.py tail --n 20
```

Each entry is SHA-256 chained to the previous one, so post-hoc edits are
detectable. Validation scripts reading the ledger should refuse to apply a
promotion when `verify` returns non-zero.

## Rollback

To revert a `live` flag:

1. Set the env var back to `shadow` (not `off` — keep the diagnostics flowing).
2. Append a ledger entry with `target NAME_MODE=shadow` and the rollback
   reason.
3. Re-run `validate_all.py --baseline ...` and confirm the regression cleared.

## Related Pages

- [[plugin-modes]] — Convention for off/shadow/live flags
- [[agent-intelligence]] — Largest current consumer of this playbook
- [[validation]] — Pipeline used by gates 2 and 3
- [[hypothesis-ledger]] — Gate 4 source
- [[canary-rollout]] — Pre-live live-trading sanity steps
- [[backtest-intelligence-overlay]] — Generates the historical PnL evidence the gates require

---
*Last compiled: 2026-04-17*
