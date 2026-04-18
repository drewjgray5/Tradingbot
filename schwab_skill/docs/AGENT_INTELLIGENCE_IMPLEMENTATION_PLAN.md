# Agent Intelligence Implementation Plan

This plan upgrades scanner and simulation intelligence with strict safety gating and measurable promotion criteria.

## Goals

- Improve decision quality while preserving current operational safety.
- Increase adaptation across market regimes without introducing unstable behavior.
- Promote only when walk-forward and shadow metrics beat baseline.

## Scope

- Dynamic multi-agent weighting in MiroFish simulation.
- Meta-policy combiner in Stage B to convert model outputs into trade intent and size bands.
- Uncertainty-aware throttling and downsize behavior.
- Counterfactual outcome tracking for rejected signals.
- Promotion gates and validation extensions.

## Architecture Changes

### 1) Dynamic Agent Weighting (engine layer)

Primary file:

- `engine_analysis.py`

Add:

- rolling persona reliability scores:
  - institutional trend
  - mean reversion
  - retail sentiment
- regime-segmented reliability buckets (bull/neutral/bear from regime v2 bucket)
- disagreement metric:
  - weighted vote variance
  - scenario probability dispersion

Output fields (simulation payload):

- `agent_weighting.version`
- `agent_weighting.mode` (`off|shadow|live`)
- `agent_weighting.weights` (per persona)
- `agent_weighting.reliability_window_n`
- `mirofish_disagreement`

### 2) Meta-Policy Combiner (decision layer)

Primary file:

- `signal_scanner.py`

Add a bounded combiner that consumes:

- base `signal_score`
- `mirofish_conviction`
- advisory `p_up_10d`
- prediction market confidence and direction
- data quality (`ok|degraded|stale|conflict`)
- regime bucket and plugin context

Output fields (per signal):

- `meta_policy` object:
  - `mode`
  - `decision` (`allow|suppress|downsize`)
  - `score_pre_meta_policy`
  - `score_delta`
  - `score_post_meta_policy`
  - `size_multiplier`
  - `uncertainty_score`
  - `reasons`

### 3) Uncertainty-Aware Controls (risk layer)

Primary files:

- `signal_scanner.py`
- `execution.py` (optional: enforce size multiplier in live path if not already centralized)

Uncertainty score components:

- normalized MiroFish disagreement
- prediction-market uncertainty (`entropy`) and low market quality
- stale/conflicting data-quality flags
- advisory confidence bucket penalties

Behavior:

- in `shadow`: annotate would-downsize / would-suppress
- in `live`: apply bounded score and size effects only

### 4) Counterfactual Memory (learning layer)

Primary files:

- `feature_store.py` (or adjacent store utility)
- `scripts/score_hypothesis_outcomes.py` (or new scorer script)

Track outcomes for:

- signals filtered by quality gates
- signals blocked by regime or event risk
- signals suppressed by meta-policy

Purpose:

- estimate false negatives and opportunity cost
- feed reliability updates for persona weighting

## New Environment Variables

Follow existing rollout convention (`off -> shadow -> live`) and keep defaults safe.

### Dynamic agent weighting

- `MIROFISH_WEIGHTING_MODE` (default `off`): `off|shadow|live`
- `MIROFISH_WEIGHTING_WINDOW_DAYS` (default `60`)
- `MIROFISH_WEIGHTING_MIN_SAMPLES` (default `30`)
- `MIROFISH_WEIGHTING_DECAY_HALF_LIFE_DAYS` (default `20`)
- `MIROFISH_WEIGHTING_MAX_MULTIPLIER` (default `1.8`)
- `MIROFISH_WEIGHTING_MIN_MULTIPLIER` (default `0.5`)

### Meta-policy combiner

- `META_POLICY_MODE` (default `off`): `off|shadow|live`
- `META_POLICY_MIN_BASE_SCORE` (default `40.0`)
- `META_POLICY_MAX_SCORE_DELTA` (default `4.0`)
- `META_POLICY_SIZE_MULT_MIN` (default `0.70`)
- `META_POLICY_SIZE_MULT_MAX` (default `1.10`)
- `META_POLICY_SUPPRESS_THRESHOLD` (default `0.25`)
- `META_POLICY_DOWNSIZE_THRESHOLD` (default `0.45`)

### Uncertainty controls

- `UNCERTAINTY_MODE` (default `off`): `off|shadow|live`
- `UNCERTAINTY_HIGH_THRESHOLD` (default `0.65`)
- `UNCERTAINTY_MED_THRESHOLD` (default `0.45`)
- `UNCERTAINTY_SCORE_DELTA_PENALTY` (default `2.0`)
- `UNCERTAINTY_SIZE_MULT_FLOOR` (default `0.75`)

### Counterfactual logging

- `COUNTERFACTUAL_LOGGING_ENABLED` (default `false`)
- `COUNTERFACTUAL_MAX_HORIZON_DAYS` (default `20`)
- `COUNTERFACTUAL_MIN_LABELED_SAMPLES` (default `100`)

## Diagnostics Additions

Add counters to scan diagnostics:

- `meta_policy_processed`
- `meta_policy_suppressed`
- `meta_policy_downsized`
- `meta_policy_applied`
- `meta_policy_shadow_actions`
- `uncertainty_high_count`
- `uncertainty_medium_count`
- `counterfactual_logged`

Add per-scan namespaces:

- `diagnostics["meta_policy"]`:
  - `enabled`
  - `mode`
  - `processed`
  - `suppressed`
  - `downsized`
  - `applied`
- `diagnostics["uncertainty"]`:
  - `mode`
  - `high_count`
  - `medium_count`

## Rollout Sequence

### Phase 0: Instrumentation only

- Add payload and diagnostics fields.
- Keep all new modes `off`.
- Verify no behavior changes in test/backtest outputs except extra metadata.

### Phase 1: Shadow intelligence

- Enable:
  - `MIROFISH_WEIGHTING_MODE=shadow`
  - `META_POLICY_MODE=shadow`
  - `UNCERTAINTY_MODE=shadow`
  - `COUNTERFACTUAL_LOGGING_ENABLED=true`
- Collect at least 2-4 weeks of shadow diagnostics.

### Phase 2: Partial live

- Promote one feature at a time:
  1. dynamic weighting
  2. uncertainty downsize
  3. meta-policy score/suppress logic
- Keep clamps tight during first live week.

### Phase 3: Full live with gates

- All promoted only if promotion criteria pass for rolling and walk-forward windows.

## Promotion Criteria

Require all conditions:

- calibration improvement:
  - lower Brier score or log loss vs baseline
- quality improvement:
  - non-negative hit-rate delta at 10d horizon
- risk stability:
  - no increase in max drawdown beyond tolerance
- operational stability:
  - no significant increase in exceptions/timeouts
- sample adequacy:
  - minimum labeled outcomes satisfied

Suggested hard guards:

- `hit_rate_delta_10d >= +0.01`
- `brier_delta <= -0.005`
- `max_drawdown_delta <= +0.50%`
- `exceptions_delta <= +5%`

## Validation Extensions

Extend validation scripts to include:

- meta-policy schema checks
- uncertainty score bounds and clamp checks
- deterministic behavior for fixed fixture inputs
- no-op guarantee in `off` modes
- shadow/live parity assertions where expected

Recommended command additions:

- `python scripts/validate_signal_quality.py`
- `python scripts/validate_plugin_modes.py`
- `python scripts/validate_execution_quality.py`
- `python scripts/validate_all.py --profile ci --skip-backtest --strict`

## Testing Plan

Unit tests:

- `tests/test_meta_policy.py`
- `tests/test_uncertainty_scoring.py`
- `tests/test_mirofish_dynamic_weighting.py`
- `tests/test_counterfactual_logging.py`

Integration tests:

- run scanner with deterministic fixtures and assert:
  - `off` mode is behavior-preserving
  - `shadow` mode changes diagnostics only
  - `live` mode applies bounded changes

## Data Contracts

Keep backward compatibility:

- do not remove existing `signal_score`, `advisory`, or `prediction_market` fields
- append new fields as optional objects
- default to safe neutral values when missing upstream components

## Failure Handling

- Any new module failure must fail safe:
  - keep signal processing alive
  - annotate diagnostics and fallback reason
  - never force live suppression on exception paths

## Implementation Checklist

- add config getters with hardcoded defaults in `config.py`
- add new diagnostics keys with zero initialization
- add meta-policy helper module (or functions in `signal_scanner.py`)
- add dynamic weighting helper in `engine_analysis.py`
- add counterfactual persistence writer and scorer integration
- add tests and validation hooks
- rollout in shadow first and review weekly summary artifact
