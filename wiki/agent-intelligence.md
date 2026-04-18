---
source: schwab_skill/agent_intelligence.py
created: 2026-04-17
updated: 2026-04-17
tags: [scanner, intelligence, meta-policy, uncertainty]
---

# Agent Intelligence

> Dynamic agent weighting, meta-policy combiner, uncertainty throttling, and
> counterfactual logging — the upgrade layer that turns the scanner from a
> rules engine into a learning system.

## Why this exists

The Stage B pipeline historically combined a fixed set of "personas" (advisory
model, MiroFish, prediction market, regime detector, sector strength) with
hand-tuned weights. That was easy to reason about but two failure modes kept
showing up in self-study:

1. **Concept drift** — a persona that worked in 2024 (e.g. PEAD) lost its edge
   in late-stage 2025, but its weight didn't change.
2. **Agreement collapse** — when every persona agreed, throughput stayed flat;
   when they disagreed sharply, the scanner still emitted the same number of
   trades, exposing the system to outsized losers.

Agent intelligence introduces four pieces that address those failure modes:

- **Vote disagreement scoring** (`compute_vote_disagreement`) measures spread
  across persona votes for a given ticker.
- **Dynamic weighting** (`resolve_dynamic_weights`) reweights personas based on
  their hit-rate by regime bucket, persisted in
  ``.agent_reliability.json``.
- **Uncertainty score** (`compute_uncertainty_score`) folds in disagreement,
  sample size, regime, and prediction-market confidence into a single ``0..1``
  score.
- **Meta-policy combiner** (`apply_meta_policy_to_signal`) modulates the final
  emitted signal: suppress, downweight, or alert based on the uncertainty band.

## Configuration

| Env var | Default | Effect |
| --- | --- | --- |
| `MIROFISH_WEIGHTING_MODE` | `off` | `off` / `shadow` / `live` for dynamic weights |
| `META_POLICY_MODE` | `off` | `off` / `shadow` / `live` for meta-policy |
| `UNCERTAINTY_MODE` | `off` | `off` / `shadow` / `live` for uncertainty throttling |
| `COUNTERFACTUAL_LOGGING_ENABLED` | `true` | Whether suppressed/fired events are logged |
| `COUNTERFACTUAL_MAX_HORIZON_DAYS` | `5` | Default forward window for scoring |

All three feature flags follow the standard
[[plugin-modes]] rollout (off → shadow → live).

## Counterfactual log

Every meta-policy decision (suppression or amplification) is appended to
``schwab_skill/.counterfactual_log.jsonl``. The
``scripts/score_counterfactual_outcomes.py`` script walks that file and
computes the realised forward return for each event, producing a
``validation_artifacts/counterfactual_scoring_<ts>.json`` summary used by the
[[promotion-playbook]].

## Promotion gates

To move any of the three modes from ``shadow`` → ``live``:

1. ≥ 30 events scored over the past 30 days.
2. Suppressed bucket avg return ≤ fired bucket avg return (policy
   suppresses worse signals than it fires).
3. No regression on the [[hypothesis-ledger]].
4. Promotion ledger entry signed via `scripts/promotion_ledger.py append`.

## Related Pages

- [[signal-scanner]] — Where the meta-policy is applied (Stage B tail)
- [[meta-policy]] — Detailed combiner math
- [[feature-store]] — Source of truth for persona reliability buckets
- [[promotion-playbook]] — Turn shadow features into live features
- [[plugin-modes]] — General off / shadow / live rollout pattern

---
*Last compiled: 2026-04-17*
