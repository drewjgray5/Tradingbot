"""Regression tests for upgraded SEC extraction and compare payloads."""

from __future__ import annotations

from sec_filing_analysis import analyze_filing_document
from sec_filing_compare import compare_analyses
from sec_filing_reader import FilingDocument, _clip_text, _normalize_text
from webapp.routes.research import _normalize_sec_compare_payload


def _doc(ticker: str, text: str) -> FilingDocument:
    return FilingDocument(
        ticker=ticker,
        cik="0000000001",
        form="10-Q",
        accession_number="0000000001000001",
        filing_date="2026-03-31",
        primary_document="doc.htm",
        filing_url="https://example.com/filing",
        source="sec",
        text=text,
        from_cache=False,
    )


def test_normalize_text_preserves_table_numbers() -> None:
    raw = """
    <html><body>
      <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Revenue</td><td>$1,234 million</td></tr>
        <tr><td>Long-term debt</td><td>$456 million</td></tr>
      </table>
    </body></html>
    """
    normalized = _normalize_text(raw)
    assert "Revenue" in normalized
    assert "1,234 million" in normalized
    assert "Long-term debt" in normalized
    assert "|" in normalized


def test_clip_text_preserves_head_middle_and_tail() -> None:
    text = ("HEAD-" * 60) + "MIDDLE_MARKER" + ("BODY-" * 60) + "TAIL_MARKER"
    clipped = _clip_text(text, max_chars=260)
    assert "MIDDLE_MARKER" in clipped
    assert "TAIL_MARKER" in clipped
    assert "[TRUNCATED FOR LENGTH]" in clipped


def test_analysis_includes_intuitive_envelope_fields() -> None:
    text = (
        "Management raised guidance for the fiscal year. "
        "The company disclosed a material weakness in internal controls. "
        "Cash and cash equivalents increased to $2.1 billion with strong liquidity. "
        "Revenue grew to $900 million while operating income reached $120 million."
    )
    analysis = analyze_filing_document(_doc("ABC", text), enable_llm=False)
    assert analysis["ok"] is True
    assert analysis["verdict"] in {"bullish", "neutral", "bearish"}
    assert isinstance(analysis["confidence"], int)
    assert 0 <= analysis["confidence"] <= 100
    assert isinstance(analysis["why"], list)
    assert isinstance(analysis["evidence"], list)
    assert isinstance(analysis["limits"], list)
    assert isinstance(analysis["summary_headline"], str)
    assert isinstance(analysis["narrative_summary"], str)


def test_compare_outputs_change_summary_and_confidence() -> None:
    left = analyze_filing_document(
        _doc(
            "AAA",
            (
                "Management raised guidance and improved outlook for next quarter. "
                "Revenue reached $1.3 billion and liquidity remains strong."
            ),
        ),
        enable_llm=False,
    )
    right = analyze_filing_document(
        _doc(
            "BBB",
            (
                "Management lowered guidance due to demand headwind and litigation risk. "
                "Long-term debt rose to $2.4 billion with higher interest expense."
            ),
        ),
        enable_llm=False,
    )
    out = compare_analyses(
        left,
        right,
        mode="ticker_vs_ticker",
        left_label="AAA",
        right_label="BBB",
    )
    assert out["ok"] is True
    assert "change_summary" in out
    assert "compare_confidence" in out
    assert isinstance(out["compare_confidence"], int)
    assert "guidance_shift" in out["change_summary"]
    assert isinstance(out["change_summary"]["evidence_ranked"], list)


def test_compare_payload_normalizer_adds_contract_fields() -> None:
    payload = {
        "ok": True,
        "mode": "ticker_vs_ticker",
        "left": {"from_cache": True, "source": "cache"},
        "right": {"from_cache": False, "source": "sec"},
        "compare": {
            "ok": True,
            "similarities": ["Shared risk terms: liquidity."],
            "differences": ["Guidance tone differs."],
        },
    }
    normalized = _normalize_sec_compare_payload(payload)
    compare = normalized["compare"]
    assert compare["analysis_mode"] == "full_text"
    assert "summary_headline" in compare
    assert "narrative_summary" in compare
    assert "change_summary" in compare
    assert "data_freshness" in compare
    assert compare["data_freshness"]["left_from_cache"] is True
