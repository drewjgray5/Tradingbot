from pathlib import Path

from hypothesis_ledger import (
    append_hypothesis,
    fingerprint_from_mapping,
    summarize_scored_hypotheses,
)


def test_append_and_summarize_roundtrip(tmp_path: Path) -> None:
    rid = append_hypothesis(
        {
            "ticker": "ZZZ",
            "source": "advisory",
            "strategy_or_model_id": "test_model",
            "input_fingerprint": fingerprint_from_mapping({"x": 1}),
            "prediction": {"direction": "long", "entry_reference_px": 100.0},
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 2.0}},
        },
        skill_dir=tmp_path,
    )
    assert rid
    s = summarize_scored_hypotheses(tmp_path)
    assert s["ledger_records"] >= 1
    assert "advisory" in s["by_source"]
