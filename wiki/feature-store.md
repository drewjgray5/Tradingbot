---
source: schwab_skill/feature_store.py
created: 2026-04-17
updated: 2026-04-17
tags: [intelligence, persistence, learning]
---

# Feature Store

> Append-only store of scanner features and downstream outcomes. Source of
> truth for backtests, advisory model retraining, and persona reliability
> buckets.

## Layout

The feature store lives under ``schwab_skill/feature_store/`` (gitignored) and
is composed of:

- `events/<YYYY>/<MM>/<DD>.parquet` — one row per Stage B candidate, with
  Stage A features, Stage B enrichment, and the chosen meta-policy action.
- `outcomes/<YYYY>/<MM>/<DD>.parquet` — forward returns at 1d/5d/10d/20d.
- `indices/persona_reliability.json` — bucketed hit rate per persona × regime,
  consumed by [[agent-intelligence]].

## Write path

`feature_store.record_event(...)` is called from `_scan_stage_b_enrich` after
a signal has been ranked. The record contains:

- ticker, timestamp, regime bucket
- Stage A and Stage B feature snapshot
- per-persona votes and confidences (from [[mirofish-engine]])
- meta-policy decision and uncertainty score
- final signal_score and rank
- chosen action (`emitted`, `suppressed`, `downweighted`)

## Read paths

- **Backtests** read events + outcomes for a date range to recompute strategy
  PnL with new parameters.
- **Self-study** joins events to outcomes nightly to update persona reliability
  buckets.
- **Counterfactual scorer** (`scripts/score_counterfactual_outcomes.py`) only
  reads the suppressed-event log; it does not query the feature store directly.

## Retention

Feature store rows are never deleted in place. The
``scripts/prune_validation_artifacts.py`` script does **not** touch the
feature store; long-horizon backtests rely on multi-year history. Use a
separate storage tier (e.g. S3 cold storage) for archival.

## Related Pages

- [[agent-intelligence]] — Reads `persona_reliability.json`
- [[mirofish-engine]] — Writes per-persona votes that get persisted here
- [[advisory-model]] — Retrained nightly from feature store rows
- [[self-study]] — Outcome attribution loop that updates the store

---
*Last compiled: 2026-04-17*
