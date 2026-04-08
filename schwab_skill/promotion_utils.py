"""
Helpers for advisory champion/challenger comparison and promotion decisions.
"""

from __future__ import annotations

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def extract_metrics(model: dict[str, Any] | None) -> dict[str, float]:
    model = model or {}
    cal = (model.get("calibration_metrics") or {}) if isinstance(model, dict) else {}
    wf = (model.get("walk_forward") or {}) if isinstance(model, dict) else {}
    wf_summary = (wf.get("summary") or {}) if isinstance(wf, dict) else {}
    auc_summary = wf_summary.get("auc") if isinstance(wf_summary.get("auc"), dict) else {}
    top20_summary = wf_summary.get("top20_hit_rate") if isinstance(wf_summary.get("top20_hit_rate"), dict) else {}
    return {
        "calibration_auc": _safe_float(cal.get("auc"), 0.0),
        "calibration_brier": _safe_float(cal.get("brier"), 1.0),
        "calibration_top20_hit_rate": _safe_float(cal.get("top20_hit_rate"), 0.0),
        "walkforward_auc_mean": _safe_float((auc_summary or {}).get("mean"), 0.0),
        "walkforward_top20_hit_rate_mean": _safe_float((top20_summary or {}).get("mean"), 0.0),
        "fold_count": _safe_float(wf.get("fold_count"), 0.0),
    }


def compare_artifacts(
    champion: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
) -> dict[str, Any]:
    cm = extract_metrics(champion)
    nm = extract_metrics(challenger)
    return {
        "champion": cm,
        "challenger": nm,
        "delta": {
            "calibration_auc": nm["calibration_auc"] - cm["calibration_auc"],
            "calibration_brier": nm["calibration_brier"] - cm["calibration_brier"],
            "calibration_top20_hit_rate": nm["calibration_top20_hit_rate"] - cm["calibration_top20_hit_rate"],
            "walkforward_auc_mean": nm["walkforward_auc_mean"] - cm["walkforward_auc_mean"],
            "walkforward_top20_hit_rate_mean": (
                nm["walkforward_top20_hit_rate_mean"] - cm["walkforward_top20_hit_rate_mean"]
            ),
            "fold_count": nm["fold_count"] - cm["fold_count"],
        },
    }


def decide_promotion(
    champion: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
    challenger_gates_passed: bool,
    min_auc_delta: float = 0.005,
    min_top20_delta: float = 0.005,
    max_brier_delta: float = 0.0,
    require_walkforward_gain: bool = True,
) -> dict[str, Any]:
    comparison = compare_artifacts(champion, challenger)
    delta = comparison["delta"]
    reasons: list[str] = []
    promote = True

    if not challenger_gates_passed:
        promote = False
        reasons.append("challenger_failed_acceptance_gates")

    if delta["calibration_auc"] < float(min_auc_delta):
        promote = False
        reasons.append(
            f"calibration_auc_delta_too_small:{delta['calibration_auc']:.6f}<{float(min_auc_delta):.6f}"
        )
    if delta["calibration_top20_hit_rate"] < float(min_top20_delta):
        promote = False
        reasons.append(
            "calibration_top20_delta_too_small:"
            f"{delta['calibration_top20_hit_rate']:.6f}<{float(min_top20_delta):.6f}"
        )
    if delta["calibration_brier"] > float(max_brier_delta):
        promote = False
        reasons.append(
            f"calibration_brier_worse:{delta['calibration_brier']:.6f}>{float(max_brier_delta):.6f}"
        )

    if require_walkforward_gain:
        if delta["walkforward_auc_mean"] < 0.0:
            promote = False
            reasons.append(f"walkforward_auc_regressed:{delta['walkforward_auc_mean']:.6f}")
        if delta["walkforward_top20_hit_rate_mean"] < 0.0:
            promote = False
            reasons.append(
                "walkforward_top20_regressed:"
                f"{delta['walkforward_top20_hit_rate_mean']:.6f}"
            )

    if promote:
        reasons.append("challenger_meets_all_promotion_thresholds")

    return {
        "promote": promote,
        "reasons": reasons,
        "comparison": comparison,
        "thresholds": {
            "min_auc_delta": float(min_auc_delta),
            "min_top20_delta": float(min_top20_delta),
            "max_brier_delta": float(max_brier_delta),
            "require_walkforward_gain": bool(require_walkforward_gain),
        },
    }
