---
source: schwab_skill/engine_analysis.py, schwab_skill/agent_intelligence.py
created: 2026-04-17
updated: 2026-04-17
tags: [intelligence, simulation, agents]
---

# MiroFish Engine

> The internal multi-agent simulation that scores each ticker from several
> "persona" perspectives (momentum, value, mean-reversion, news flow, etc.).

## Concept

The MiroFish engine runs a set of lightweight personas against the same
candidate ticker, then aggregates their votes into a single ``mirofish_conviction``
field on the signal. Each persona reads only the features it cares about,
which keeps the engine cheap to run during Stage B even on hundreds of
candidates.

## Persona contract

A persona is a callable that takes:

- the candidate dataframe
- the diagnostics dict (mutable; can record reason codes)
- a context dict with already-computed Stage A values

…and returns a numeric vote in `[-1, +1]` plus a confidence in `[0, 1]`.

## Dynamic weighting

Persona votes are no longer averaged with fixed weights. The
[[agent-intelligence]] module reads
``.agent_reliability.json`` (per-regime hit rate) and resolves a weight vector
per scan via `resolve_dynamic_weights`. When the regime changes (e.g. SPY
breaks below the 200-day SMA → bear), the weight vector shifts toward
personas that historically performed in that regime.

## Sim outputs

Sim runs are written to ``mirofish_sims/`` (gitignored). Each subdirectory
contains:

- `decisions.json` — final vote per ticker.
- `traces/<ticker>.json` — per-persona votes and confidences.
- `weights.json` — resolved dynamic weights for that scan.

## Related Pages

- [[agent-intelligence]] — Dynamic weighting & meta-policy that consume MiroFish
- [[meta-policy]] — How MiroFish output is folded into the final score
- [[feature-store]] — Where reliability buckets live
- [[signal-scanner]] — Stage B integration point

---
*Last compiled: 2026-04-17*
