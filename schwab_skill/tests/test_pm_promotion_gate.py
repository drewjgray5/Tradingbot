from __future__ import annotations

from scripts.decide_pm_promotion import decide_promotion


def test_pm_promotion_gate_passes_when_holdout_and_regimes_are_strong() -> None:
    payload = {
        "mode": "ab_walkforward",
        "windows": [
            {"is_holdout": False, "treatment": {"max_drawdown_net_pct": -8.0}},
            {"is_holdout": False, "treatment": {"max_drawdown_net_pct": -7.5}},
        ],
        "holdout": {
            "paired": {
                "mean_net_return_delta": 0.01,
                "mean_net_return_delta_ci95": [0.001, 0.02],
            },
            "treatment": {"max_drawdown_net_pct": -9.0},
        },
        "aggregates": {
            "regime_slices": {
                "bull": {"mean_delta": 0.01},
                "chop": {"mean_delta": 0.002},
                "bear": {"mean_delta": -0.001},
            }
        },
    }
    promote, reasons, checks = decide_promotion(
        payload,
        max_dd_blowout_pct=2.5,
        min_regime_buckets=2,
        max_ci_below_zero=0.0005,
    )
    assert promote is True
    assert "promotion_gates_passed" in reasons
    assert int(checks["positive_regime_buckets"]) >= 2

