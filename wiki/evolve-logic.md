---
source: schwab_skill/evolve_logic.py
created: 2026-04-17
updated: 2026-04-17
tags: [intelligence, learning, post-mortem, env-tuning]
---

# Evolve Logic

> Post-mortem analysis loop: correlates realised P/L with scanner
> features, identifies which thresholds predict failure in the current
> regime, and proposes (or applies) `.env` tweaks via
> `strategy_update.json`.

## Why this exists

Static thresholds drift. The volume-ratio gate that worked in 2024
might be too lax in a low-volume regime, or the forensic Sloan ratio
cap might be cutting too many candidates in the current sector
rotation. Evolve Logic answers "which knobs are predictive of failure
right now, and by how much should we move them?" without humans
hand-tuning.

## Inputs

- `.trade_outcomes.json` — realised P/L per closed trade.
- [[feature-store]] — per-scan feature snapshot for every emitted
  signal (matched to outcomes by ticker + timestamp).

## Method

1. Load outcomes; bucket each trade as ``win`` / ``loss``.
2. Join to the corresponding feature row from the feature store.
3. Train a Random Forest classifier on the joined dataset (label =
   win/loss; features = the curated `TUNABLE_FEATURE_MAP`).
4. Compute per-feature importance + marginal direction.
5. For each high-importance feature: if its current threshold is on
   the wrong side of the empirical winners' distribution, propose a
   one-step adjustment (`adjust_step`) bounded by `min_bound`/
   `max_bound`.
6. Write the proposal to ``strategy_update.json``. With ``--apply``,
   patch the corresponding env keys; without, leave the file as a
   review artefact.

## Tunable feature map

Each entry in `TUNABLE_FEATURE_MAP` declares:

- `env_key` — the env var that controls this threshold.
- `direction` — `"higher_is_better"` or `"lower_is_better"`; tells the
  loop which way to step when the data says "be stricter".
- `current_default` / `adjust_step` / `min_bound` / `max_bound` —
  guardrails so a single adverse run can't drag a knob to extremes.

Today the map covers `volume_ratio`, `signal_score`, `forensic_sloan`,
and a handful of others. Adding a new tunable is just a new entry.

## Workflow

```bash
python evolve_logic.py             # dry-run analysis + propose update
python evolve_logic.py --apply     # propose AND patch .env
python evolve_logic.py --dry-run   # analysis only, no file writes
```

The proposed update is intentionally separate from
[[promotion-playbook]] approval. A ``strategy_update.json`` lands as
"shadow" until an operator inspects it and either applies it manually
or wires it through ``decide_strategy_promotion.py`` (which then
demands a [[promotion-playbook]]-grade signed ledger entry — see
``scripts/promotion_ledger.py``).

## Relationship to the rest of the learning stack

- [[feature-store]] — Source of truth for the inputs.
- [[hypothesis-ledger]] — Tracks decision quality over time; evolve
  logic should not propose changes that contradict an open hypothesis.
- [[self-study]] — Different audience: self-study writes nightly
  outcome attribution; evolve logic consumes that to suggest knob
  changes.
- [[promotion-playbook]] — Required gate before any proposed change
  becomes live.

## Related Pages

- [[feature-store]] — Inputs
- [[self-study]] — Sibling outcome-attribution loop
- [[promotion-playbook]] — Required approval flow
- [[scanner-tunables]] — The env knobs evolve-logic targets

---

*Last compiled: 2026-04-17*
