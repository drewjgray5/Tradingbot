from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
RELIABILITY_FILE = ".agent_reliability.json"
COUNTERFACTUAL_LOG_FILE = ".counterfactual_log.jsonl"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _reliability_path(skill_dir: Path) -> Path:
    return skill_dir / RELIABILITY_FILE


def _load_reliability(skill_dir: Path) -> dict[str, Any]:
    path = _reliability_path(skill_dir)
    if not path.exists():
        return {"buckets": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"buckets": {}}
    if isinstance(payload, dict):
        return payload
    return {"buckets": {}}


def _get_bucket_key(*, regime_is_bullish: bool | None) -> str:
    if regime_is_bullish is True:
        return "bull"
    if regime_is_bullish is False:
        return "bear"
    return "unknown"


def compute_vote_disagreement(agent_votes: list[dict[str, Any]]) -> float:
    if not agent_votes:
        return 0.0
    scores = [_safe_float(v.get("score"), 0.0) for v in agent_votes]
    mean = sum(scores) / max(1, len(scores))
    variance = sum((s - mean) ** 2 for s in scores) / max(1, len(scores))
    score_dispersion = _clamp((variance ** 0.5) / 100.0, 0.0, 1.0)

    cont_probs = [_safe_float(v.get("continuation_probability"), 0.5) for v in agent_votes]
    if cont_probs:
        prob_dispersion = _clamp(max(cont_probs) - min(cont_probs), 0.0, 1.0)
    else:
        prob_dispersion = 0.0
    return round(_clamp((score_dispersion * 0.6) + (prob_dispersion * 0.4), 0.0, 1.0), 6)


def resolve_dynamic_weights(
    *,
    base_weights: dict[str, float],
    skill_dir: Path,
    regime_is_bullish: bool | None,
) -> tuple[dict[str, float], dict[str, Any]]:
    from config import (
        get_mirofish_weighting_decay_half_life_days,
        get_mirofish_weighting_max_multiplier,
        get_mirofish_weighting_min_multiplier,
        get_mirofish_weighting_min_samples,
        get_mirofish_weighting_mode,
        get_mirofish_weighting_window_days,
    )

    mode = get_mirofish_weighting_mode(skill_dir)
    window_days = get_mirofish_weighting_window_days(skill_dir)
    min_samples = get_mirofish_weighting_min_samples(skill_dir)
    half_life_days = get_mirofish_weighting_decay_half_life_days(skill_dir)
    min_mult = get_mirofish_weighting_min_multiplier(skill_dir)
    max_mult = get_mirofish_weighting_max_multiplier(skill_dir)

    metadata: dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "regime_bucket": _get_bucket_key(regime_is_bullish=regime_is_bullish),
        "weights": dict(base_weights),
        "reliability_window_n": int(window_days),
        "min_samples": int(min_samples),
        "half_life_days": float(half_life_days),
        "applied": False,
    }
    if mode == "off":
        return dict(base_weights), metadata

    payload = _load_reliability(skill_dir)
    bucket_key = metadata["regime_bucket"]
    bucket = ((payload.get("buckets") or {}).get(bucket_key) or {})
    out: dict[str, float] = {}
    details: dict[str, Any] = {}
    for persona, base_w in base_weights.items():
        person = (bucket.get(persona) or {})
        reliability = _clamp(_safe_float(person.get("reliability"), 0.5), 0.0, 1.0)
        samples = int(_safe_float(person.get("samples"), 0.0))
        if samples < min_samples:
            multiplier = 1.0
        else:
            # reliability=0.5 => neutral multiplier of 1.0
            multiplier = 1.0 + ((reliability - 0.5) * 2.0 * 0.5)
            multiplier = _clamp(multiplier, min_mult, max_mult)
        out[persona] = max(0.01, float(base_w) * float(multiplier))
        details[persona] = {
            "reliability": reliability,
            "samples": samples,
            "multiplier": round(multiplier, 6),
        }

    wsum = sum(out.values())
    if wsum <= 0:
        out = dict(base_weights)
        wsum = sum(out.values()) or 1.0
    normalized = {k: float(v) / float(wsum) for k, v in out.items()}
    metadata["weights"] = {k: round(v, 6) for k, v in normalized.items()}
    metadata["details"] = details
    metadata["applied"] = mode == "live"
    return normalized, metadata


def compute_uncertainty_score(signal: dict[str, Any]) -> dict[str, Any]:
    miro = signal.get("mirofish_result") or {}
    pm = signal.get("prediction_market") or {}
    advisory = signal.get("advisory") or {}

    disagreement = _clamp(_safe_float(signal.get("mirofish_disagreement")), 0.0, 1.0)
    if disagreement <= 0:
        votes = miro.get("agent_votes")
        if isinstance(votes, list):
            disagreement = compute_vote_disagreement(votes)

    pm_features = pm.get("features") if isinstance(pm, dict) else {}
    pm_uncertainty = _clamp(_safe_float((pm_features or {}).get("pm_uncertainty"), 1.0), 0.0, 1.0)
    pm_quality = _clamp(_safe_float((pm_features or {}).get("pm_market_quality_score"), 0.0), 0.0, 1.0)
    pm_component = _clamp((pm_uncertainty * 0.7) + ((1.0 - pm_quality) * 0.3), 0.0, 1.0)

    dq = str(signal.get("_data_quality") or "").strip().lower()
    if dq in {"conflict", "stale"}:
        dq_component = 1.0
    elif dq == "degraded":
        dq_component = 0.6
    else:
        dq_component = 0.0

    conf_bucket = str(advisory.get("confidence_bucket") or "low").lower()
    adv_component = {"high": 0.2, "medium": 0.5}.get(conf_bucket, 0.8)
    score = _clamp((disagreement * 0.4) + (pm_component * 0.3) + (dq_component * 0.2) + (adv_component * 0.1), 0.0, 1.0)
    return {
        "score": round(score, 6),
        "mirofish_disagreement": round(disagreement, 6),
        "prediction_market_component": round(pm_component, 6),
        "data_quality_component": round(dq_component, 6),
        "advisory_component": round(adv_component, 6),
    }


def apply_meta_policy_to_signal(
    *,
    signal: dict[str, Any],
    diagnostics: dict[str, Any],
    skill_dir: Path,
) -> tuple[dict[str, Any], bool]:
    from config import (
        get_meta_policy_downsize_threshold,
        get_meta_policy_max_score_delta,
        get_meta_policy_min_base_score,
        get_meta_policy_mode,
        get_meta_policy_size_mult_max,
        get_meta_policy_size_mult_min,
        get_meta_policy_suppress_threshold,
        get_uncertainty_high_threshold,
        get_uncertainty_med_threshold,
        get_uncertainty_mode,
        get_uncertainty_score_delta_penalty,
        get_uncertainty_size_mult_floor,
    )

    mode = get_meta_policy_mode(skill_dir)
    uncertainty_mode = get_uncertainty_mode(skill_dir)
    diagnostics["meta_policy_processed"] = int(diagnostics.get("meta_policy_processed", 0) or 0) + 1

    out = dict(signal)
    base_score = _clamp(_safe_float(signal.get("signal_score"), 0.0), 0.0, 100.0)
    miro = _safe_float(signal.get("mirofish_conviction"), 0.0) / 100.0
    advisory = signal.get("advisory") or {}
    adv = (_safe_float(advisory.get("p_up_10d"), 0.5) - 0.5) * 2.0
    pm_overlay = ((signal.get("prediction_market") or {}).get("overlay") or {})
    pm_conf = _clamp(_safe_float(pm_overlay.get("confidence"), 0.0), 0.0, 1.0)
    pm_dir = _safe_float(pm_overlay.get("score_delta"), 0.0)
    directional = _clamp((miro * 0.45) + (adv * 0.35) + (_clamp(pm_dir / 4.0, -1.0, 1.0) * 0.20), -1.0, 1.0)

    max_delta = max(0.0, get_meta_policy_max_score_delta(skill_dir))
    score_delta = directional * max_delta * _clamp(0.5 + (pm_conf * 0.5), 0.2, 1.0)
    uncertainty = compute_uncertainty_score(out)
    uncertainty_score = _clamp(_safe_float(uncertainty.get("score"), 0.0), 0.0, 1.0)
    decision = "allow"
    reasons: list[str] = []
    size_mult = 1.0

    high_thr = get_uncertainty_high_threshold(skill_dir)
    med_thr = get_uncertainty_med_threshold(skill_dir)
    if uncertainty_score >= high_thr:
        diagnostics["uncertainty_high_count"] = int(diagnostics.get("uncertainty_high_count", 0) or 0) + 1
        reasons.append("uncertainty_high")
    elif uncertainty_score >= med_thr:
        diagnostics["uncertainty_medium_count"] = int(diagnostics.get("uncertainty_medium_count", 0) or 0) + 1
        reasons.append("uncertainty_medium")

    if uncertainty_mode != "off" and uncertainty_score >= med_thr:
        score_delta -= abs(get_uncertainty_score_delta_penalty(skill_dir))
        reasons.append("uncertainty_penalty")
        size_mult = min(size_mult, max(get_uncertainty_size_mult_floor(skill_dir), 0.1))

    if base_score < get_meta_policy_min_base_score(skill_dir):
        reasons.append("baseline_score_low")
        score_delta = min(score_delta, 0.0)

    suppress_threshold = get_meta_policy_suppress_threshold(skill_dir)
    downsize_threshold = get_meta_policy_downsize_threshold(skill_dir)
    if uncertainty_score >= max(suppress_threshold, downsize_threshold):
        decision = "suppress"
        reasons.append("meta_policy_suppress")
    elif uncertainty_score >= downsize_threshold:
        decision = "downsize"
        reasons.append("meta_policy_downsize")

    size_mult = _clamp(size_mult + (directional * 0.10), get_meta_policy_size_mult_min(skill_dir), get_meta_policy_size_mult_max(skill_dir))
    score_post = _clamp(base_score + score_delta, 0.0, 100.0)
    meta_payload = {
        "mode": mode,
        "decision": decision,
        "score_pre_meta_policy": round(base_score, 4),
        "score_delta": round(score_delta, 4),
        "score_post_meta_policy": round(score_post, 4),
        "size_multiplier": round(size_mult, 4),
        "uncertainty_score": uncertainty_score,
        "uncertainty_components": uncertainty,
        "reasons": reasons,
    }
    out["meta_policy"] = meta_payload

    if mode == "live":
        out["signal_score"] = score_post
        out["meta_policy_size_multiplier"] = round(size_mult, 4)
        if decision == "suppress":
            diagnostics["meta_policy_suppressed"] = int(diagnostics.get("meta_policy_suppressed", 0) or 0) + 1
            diagnostics["meta_policy_applied"] = int(diagnostics.get("meta_policy_applied", 0) or 0) + 1
            return out, False
        if decision == "downsize":
            diagnostics["meta_policy_downsized"] = int(diagnostics.get("meta_policy_downsized", 0) or 0) + 1
            diagnostics["meta_policy_applied"] = int(diagnostics.get("meta_policy_applied", 0) or 0) + 1
        elif abs(score_delta) > 0:
            diagnostics["meta_policy_applied"] = int(diagnostics.get("meta_policy_applied", 0) or 0) + 1
        return out, True

    if mode == "shadow":
        out["meta_policy"]["shadow_action"] = decision
        diagnostics["meta_policy_shadow_actions"] = int(diagnostics.get("meta_policy_shadow_actions", 0) or 0) + 1
    return out, True


def log_counterfactual_event(
    *,
    signal: dict[str, Any],
    reason: str,
    skill_dir: Path,
) -> bool:
    from config import get_counterfactual_logging_enabled, get_counterfactual_max_horizon_days

    if not get_counterfactual_logging_enabled(skill_dir):
        return False

    path = skill_dir / COUNTERFACTUAL_LOG_FILE
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": str(signal.get("ticker") or "").upper(),
        "reason": str(reason or "unknown"),
        "horizon_days": int(get_counterfactual_max_horizon_days(skill_dir)),
        "signal_score": _safe_float(signal.get("signal_score"), 0.0),
        "mirofish_conviction": _safe_float(signal.get("mirofish_conviction"), 0.0),
        "meta_policy": signal.get("meta_policy"),
        "prediction_market": signal.get("prediction_market"),
        "advisory": signal.get("advisory"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
        return True
    except Exception as exc:
        LOG.debug("Counterfactual log write failed for %s: %s", payload.get("ticker"), exc)
        return False
