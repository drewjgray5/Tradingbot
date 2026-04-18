from __future__ import annotations

import json

from agent_intelligence import (
    apply_meta_policy_to_signal,
    compute_uncertainty_score,
    compute_vote_disagreement,
    resolve_dynamic_weights,
)


def test_compute_vote_disagreement_bounds() -> None:
    votes = [
        {"score": 80, "continuation_probability": 0.8},
        {"score": -60, "continuation_probability": 0.2},
        {"score": 10, "continuation_probability": 0.55},
    ]
    d = compute_vote_disagreement(votes)
    assert 0.0 <= float(d) <= 1.0
    assert float(d) > 0.0


def test_resolve_dynamic_weights_live_uses_reliability(tmp_path) -> None:
    (tmp_path / ".env").write_text("\n".join(["MIROFISH_WEIGHTING_MODE=live", "MIROFISH_WEIGHTING_MIN_SAMPLES=10"]))
    (tmp_path / ".agent_reliability.json").write_text(
        json.dumps(
            {
                "buckets": {
                    "bull": {
                        "institutional_trend": {"reliability": 0.8, "samples": 50},
                        "mean_reversion": {"reliability": 0.4, "samples": 50},
                        "retail_fomo": {"reliability": 0.5, "samples": 50},
                    }
                }
            }
        )
    )
    weights, meta = resolve_dynamic_weights(
        base_weights={"institutional_trend": 1.0, "mean_reversion": 1.0, "retail_fomo": 1.0},
        skill_dir=tmp_path,
        regime_is_bullish=True,
    )
    assert meta["mode"] == "live"
    assert bool(meta["applied"]) is True
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert float(weights["institutional_trend"]) > float(weights["mean_reversion"])


def test_meta_policy_shadow_keeps_signal(tmp_path) -> None:
    (tmp_path / ".env").write_text("\n".join(["META_POLICY_MODE=shadow", "UNCERTAINTY_MODE=shadow"]))
    diagnostics: dict[str, int] = {"meta_policy_processed": 0}
    signal = {
        "ticker": "AAPL",
        "signal_score": 62.0,
        "mirofish_conviction": 40.0,
        "mirofish_disagreement": 0.7,
        "prediction_market": {"overlay": {"confidence": 0.6, "score_delta": 1.0}, "features": {"pm_uncertainty": 0.7, "pm_market_quality_score": 0.5}},
        "advisory": {"p_up_10d": 0.57, "confidence_bucket": "medium"},
        "_data_quality": "ok",
    }
    out, keep = apply_meta_policy_to_signal(signal=signal, diagnostics=diagnostics, skill_dir=tmp_path)
    assert keep is True
    assert out.get("meta_policy") is not None
    assert out["meta_policy"]["mode"] == "shadow"
    assert out["meta_policy"].get("shadow_action") in {"allow", "downsize", "suppress"}
    assert float(out["signal_score"]) == 62.0


def test_compute_uncertainty_uses_components() -> None:
    signal = {
        "mirofish_disagreement": 0.5,
        "prediction_market": {"features": {"pm_uncertainty": 0.9, "pm_market_quality_score": 0.2}},
        "advisory": {"confidence_bucket": "low"},
        "_data_quality": "degraded",
    }
    out = compute_uncertainty_score(signal)
    assert 0.0 <= float(out["score"]) <= 1.0
    assert float(out["prediction_market_component"]) > 0.0
