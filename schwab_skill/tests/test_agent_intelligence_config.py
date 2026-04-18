from __future__ import annotations

from config import (
    get_counterfactual_logging_enabled,
    get_meta_policy_mode,
    get_meta_policy_size_mult_max,
    get_meta_policy_size_mult_min,
    get_mirofish_weighting_mode,
    get_uncertainty_mode,
)


def test_agent_intelligence_config_defaults(tmp_path) -> None:
    assert get_mirofish_weighting_mode(tmp_path) == "off"
    assert get_meta_policy_mode(tmp_path) == "off"
    assert get_uncertainty_mode(tmp_path) == "off"
    assert get_counterfactual_logging_enabled(tmp_path) is False
    assert float(get_meta_policy_size_mult_min(tmp_path)) <= 1.0
    assert float(get_meta_policy_size_mult_max(tmp_path)) >= 1.0


def test_agent_intelligence_mode_parsing(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MIROFISH_WEIGHTING_MODE=live",
                "META_POLICY_MODE=shadow",
                "UNCERTAINTY_MODE=live",
                "COUNTERFACTUAL_LOGGING_ENABLED=true",
            ]
        )
    )
    assert get_mirofish_weighting_mode(tmp_path) == "live"
    assert get_meta_policy_mode(tmp_path) == "shadow"
    assert get_uncertainty_mode(tmp_path) == "live"
    assert get_counterfactual_logging_enabled(tmp_path) is True
