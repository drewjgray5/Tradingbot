---
source: schwab_skill/agent_intelligence.py
created: 2026-04-17
updated: 2026-04-17
tags: [intelligence, scoring, scanner]
---

# Meta-Policy Combiner

> Final-stage decision layer that takes the scanner's raw signal score plus
> the uncertainty score and decides whether to emit, suppress, or downweight
> the signal.

## Inputs

For each candidate the combiner receives:

- `signal_score` — the composite score from Stage B ranking.
- `uncertainty` — `0..1` uncertainty produced by `compute_uncertainty_score`
  (vote disagreement, regime confidence, prediction-market confidence,
  sample-size penalty).
- `persona_votes` — per-persona vote/confidence pairs (from
  [[mirofish-engine]]).
- `regime` — current regime bucket (`bull`, `bull_chop`, `bear_rally`, `bear`,
  …).

## Decision space

| Action | Trigger |
| --- | --- |
| `emit` | uncertainty ≤ `META_POLICY_EMIT_MAX_UNCERTAINTY` (default 0.45) |
| `downweight` | uncertainty ∈ (emit_max, suppress_min); score is multiplied by `1 - uncertainty * META_POLICY_DOWNWEIGHT_FACTOR` |
| `suppress` | uncertainty ≥ `META_POLICY_SUPPRESS_MIN_UNCERTAINTY` (default 0.75) |
| `boost` | high agreement & high regime confidence; bumps score by `META_POLICY_BOOST_PCT` (default 5%) |

All thresholds live in [[../schwab_skill/config|config.py]] and follow the
standard env-var pattern.

## Modes

`META_POLICY_MODE` is `off` / `shadow` / `live` per the [[plugin-modes]]
convention.

- `off` — the combiner is bypassed; signals flow as today.
- `shadow` — the combiner records its decision into
  `signal["meta_policy"] = {"action": "suppress", "uncertainty": ...}`
  but does **not** actually modify the signal.
- `live` — the action is applied (signal suppressed / downweighted / boosted).

Counterfactual events are logged regardless of mode (provided
`COUNTERFACTUAL_LOGGING_ENABLED=true`) so the [[promotion-playbook]] gates
can be evaluated even from shadow data.

## Worked example

Suppose Stage B emits `MSFT` with `signal_score=72`, four persona votes of
`+0.8, +0.9, -0.4, +0.6`, regime `bull_chop`. The combiner:

1. computes disagreement = std-dev of votes ≈ 0.55,
2. uncertainty = clamp(0.55*0.5 + (1 - regime_confidence)*0.3 + (1 - pm_confidence)*0.2, 0, 1) ≈ 0.52,
3. action = `downweight` (above emit threshold, below suppress threshold),
4. adjusted score = 72 * (1 - 0.52 * 0.4) ≈ 57.

In shadow mode the dashboard shows both 72 (raw) and 57 (would-be), so
operators can A/B without flipping the live switch.

## Related Pages

- [[agent-intelligence]] — Parent module
- [[mirofish-engine]] — Source of persona votes
- [[promotion-playbook]] — Promotion gates
- [[signal-scanner]] — Where the combiner runs in the pipeline

---
*Last compiled: 2026-04-17*
