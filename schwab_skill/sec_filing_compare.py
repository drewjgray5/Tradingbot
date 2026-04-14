"""
Compare-and-contrast helpers for SEC filing analyses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sec_filing_analysis import analyze_filing_document
from sec_filing_reader import fetch_recent_filings, read_filing_document


def _list_overlap(left: list[str], right: list[str]) -> tuple[list[str], list[str], list[str]]:
    left_set = set(left or [])
    right_set = set(right or [])
    common = sorted(left_set.intersection(right_set))
    left_only = sorted(left_set.difference(right_set))
    right_only = sorted(right_set.difference(left_set))
    return common, left_only, right_only


def _kpi_count_map(analysis: dict[str, Any]) -> dict[str, int]:
    kpis = analysis.get("kpi_signals") or {}
    return {
        "revenue_mentions": len(kpis.get("revenue_mentions") or []),
        "profit_mentions": len(kpis.get("profit_mentions") or []),
        "cashflow_mentions": len(kpis.get("cashflow_mentions") or []),
        "debt_mentions": len(kpis.get("debt_mentions") or []),
        "r_and_d_mentions": len(kpis.get("r_and_d_mentions") or []),
        "liquidity_mentions": len(kpis.get("liquidity_mentions") or []),
    }


def _metric_deltas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, int]:
    left_counts = _kpi_count_map(left)
    right_counts = _kpi_count_map(right)
    out: dict[str, int] = {}
    for key in left_counts.keys():
        out[key] = int(left_counts.get(key, 0)) - int(right_counts.get(key, 0))
    return out


def _rank_evidence(left: dict[str, Any], right: dict[str, Any], max_items: int = 6) -> list[dict[str, str]]:
    ranked: list[dict[str, str]] = []
    for label, payload in (("left", left), ("right", right)):
        for item in list(payload.get("evidence") or [])[: max(1, max_items // 2)]:
            if not isinstance(item, dict):
                continue
            ranked.append(
                {
                    "side": label,
                    "claim": str(item.get("claim") or ""),
                    "term": str(item.get("term") or ""),
                    "quote": str(item.get("quote") or "")[:240],
                    "section_hint": str(item.get("section_hint") or ""),
                }
            )
            if len(ranked) >= max_items:
                return ranked
    return ranked


def _compare_confidence(left: dict[str, Any], right: dict[str, Any], differences: list[str]) -> int:
    left_conf = float(left.get("confidence", 50) or 50)
    right_conf = float(right.get("confidence", 50) or 50)
    score = (left_conf + right_conf) / 2.0
    if not differences:
        score -= 8.0
    elif len(differences) >= 3:
        score += 5.0
    return int(max(5.0, min(95.0, score)))


def compare_analyses(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    mode: str,
    left_label: str,
    right_label: str,
    highlight_changes_only: bool = False,
) -> dict[str, Any]:
    left_themes = left.get("key_themes") or []
    right_themes = right.get("key_themes") or []
    left_risks = left.get("risk_terms") or []
    right_risks = right.get("risk_terms") or []
    common_risks, left_risk_only, right_risk_only = _list_overlap(left_risks, right_risks)
    common_themes, _, _ = _list_overlap(left_themes, right_themes)

    deltas = _metric_deltas(left, right)
    materially_changed: list[str] = []
    for key, value in deltas.items():
        if abs(int(value)) >= 2:
            direction = "higher" if value > 0 else "lower"
            materially_changed.append(f"{left_label} has {direction} {key.replace('_', ' ')} ({value:+d}).")

    similarities: list[str] = []
    if common_risks:
        similarities.append(f"Shared risk terms: {', '.join(common_risks[:6])}.")
    if common_themes:
        similarities.append("Both filings emphasize overlapping themes in operations/outlook.")
    if not similarities:
        similarities.append("Limited direct overlap; narratives are materially different.")

    differences: list[str] = []
    if left_risk_only:
        differences.append(f"{left_label}-specific risks: {', '.join(left_risk_only[:6])}.")
    if right_risk_only:
        differences.append(f"{right_label}-specific risks: {', '.join(right_risk_only[:6])}.")
    if left.get("guidance_signal") != right.get("guidance_signal"):
        differences.append(
            f"Guidance tone differs: {left_label}={left.get('guidance_signal')} vs {right_label}={right.get('guidance_signal')}."
        )
    if not differences:
        differences.append("Guidance and risk posture are broadly similar at a high level.")

    if highlight_changes_only:
        similarities = ["Boilerplate overlap hidden (highlighting edits and deltas only)."]

    legal_flag_terms = (
        "material weakness",
        "litigation",
        "investigation",
        "restatement",
        "default",
        "bankruptcy",
        "subpoena",
        "contingency",
        "impairment",
        "internal control",
    )
    left_risk_only_set = set(left_risk_only)
    right_risk_only_set = set(right_risk_only)
    red_flag_ledger: list[str] = []
    new_left_legal = [term for term in left_risk_only_set if any(k in term for k in legal_flag_terms)]
    if new_left_legal:
        red_flag_ledger.append(f"New {left_label} legal/risk disclosures: {', '.join(sorted(new_left_legal)[:6])}.")
    new_right_legal = [term for term in right_risk_only_set if any(k in term for k in legal_flag_terms)]
    if new_right_legal:
        red_flag_ledger.append(f"New {right_label} legal/risk disclosures: {', '.join(sorted(new_right_legal)[:6])}.")
    if not red_flag_ledger:
        red_flag_ledger.append("No clearly new legal-language risk terms detected vs prior comparator.")

    left_counts = _kpi_count_map(left)
    right_counts = _kpi_count_map(right)
    left_revenue_refs = int(left_counts.get("revenue_mentions", 0))
    right_revenue_refs = int(right_counts.get("revenue_mentions", 0))
    left_rnd_refs = int(left_counts.get("r_and_d_mentions", 0))
    right_rnd_refs = int(right_counts.get("r_and_d_mentions", 0))

    def _growth_style(revenue_refs: int, rnd_refs: int) -> str:
        if revenue_refs > rnd_refs + 1:
            return "more growth-language than innovation-language (possible demand-led or sales-led emphasis)"
        if rnd_refs > revenue_refs + 1:
            return "more innovation-language than growth-language (possible moat-building emphasis)"
        return "balanced growth vs innovation disclosure mix"

    margin_moat_bullets = [
        (
            f"{left_label}: revenue references={left_revenue_refs}, "
            f"R&D references={left_rnd_refs} -> {_growth_style(left_revenue_refs, left_rnd_refs)}."
        ),
        (
            f"{right_label}: revenue references={right_revenue_refs}, "
            f"R&D references={right_rnd_refs} -> {_growth_style(right_revenue_refs, right_rnd_refs)}."
        ),
    ]

    def _trajectory_score(analysis: dict[str, Any], rev_refs: int, rnd_refs: int, risk_only_count: int) -> float:
        guidance = str(analysis.get("guidance_signal") or "neutral").lower()
        score = float(rev_refs * 1.2 + rnd_refs * 1.6 - (risk_only_count * 1.8))
        if guidance == "positive":
            score += 2.0
        elif guidance == "negative":
            score -= 2.0
        return score

    left_score = _trajectory_score(left, left_revenue_refs, left_rnd_refs, len(left_risk_only))
    right_score = _trajectory_score(right, right_revenue_refs, right_rnd_refs, len(right_risk_only))
    stronger = left_label if left_score >= right_score else right_label
    weaker = right_label if stronger == left_label else left_label

    verdict_gap = abs(left_score - right_score)
    if verdict_gap >= 2.5:
        sentiment_tag = "[BULLISH CHANGE]" if stronger == left_label else "[BEARISH CHANGE]"
    elif verdict_gap >= 1.0:
        sentiment_tag = "[NEUTRAL/BOILERPLATE]"
    else:
        sentiment_tag = "[NEUTRAL/BOILERPLATE]"

    tldr_verdict = (
        f"{stronger} shows the stronger operational trajectory vs {weaker}, "
        f"with a cleaner risk delta and better growth-vs-innovation balance in current disclosures."
    )

    forensic_divergence = {
        "sentiment_tag": sentiment_tag,
        "red_flag_ledger": red_flag_ledger,
        "margin_moat_check": {
            "left_label": left_label,
            "right_label": right_label,
            "left_revenue_refs": left_revenue_refs,
            "left_r_and_d_refs": left_rnd_refs,
            "right_revenue_refs": right_revenue_refs,
            "right_r_and_d_refs": right_rnd_refs,
            "bullets": margin_moat_bullets,
        },
        "tldr_verdict": tldr_verdict,
    }

    investor_takeaway = (
        "Differences appear meaningful in disclosure tone and risk profile."
        if materially_changed or left_risk_only or right_risk_only
        else "Filings look directionally similar; use valuation/technical context for differentiation."
    )
    summary_headline = (
        "Clear filing divergence: risk and tone differ meaningfully."
        if materially_changed or left_risk_only or right_risk_only
        else "Filings are broadly aligned at a high level."
    )
    top_differences = differences[:3]
    top_commonalities = similarities[:3]
    narrative_summary = (
        f"{forensic_divergence['sentiment_tag']} "
        f"Red Flag Ledger: {red_flag_ledger[0]} "
        f"Margin & Moat: {margin_moat_bullets[0]} "
        f"TL;DR Verdict: {tldr_verdict}"
    )
    guidance_shift = "unchanged"
    if left.get("guidance_signal") != right.get("guidance_signal"):
        guidance_shift = f"{right.get('guidance_signal')} -> {left.get('guidance_signal')}"
    evidence_ranked = _rank_evidence(left, right, max_items=6)
    compare_confidence = _compare_confidence(left, right, differences)
    rationale: list[str] = []
    rationale.append(f"Guidance shift: {guidance_shift}.")
    rationale.append(
        f"Risk delta: +{len(left_risk_only)} unique to {left_label}, +{len(right_risk_only)} unique to {right_label}."
    )
    rationale.append(f"Evidence snippets considered: {len(evidence_ranked)}.")
    change_summary = {
        "new_risks": left_risk_only[:8],
        "resolved_risks": right_risk_only[:8],
        "guidance_shift": guidance_shift,
        "evidence_ranked": evidence_ranked,
        "plain_english_rationale": rationale,
    }
    return {
        "ok": True,
        "mode": mode,
        "left_label": left_label,
        "right_label": right_label,
        "highlight_changes_only": bool(highlight_changes_only),
        "sentiment_tag": forensic_divergence["sentiment_tag"],
        "similarities": similarities,
        "differences": differences,
        "metric_deltas": deltas,
        "material_changes": materially_changed,
        "forensic_divergence": forensic_divergence,
        "summary_headline": summary_headline,
        "narrative_summary": narrative_summary,
        "top_differences": top_differences,
        "top_commonalities": top_commonalities,
        "investor_takeaway": investor_takeaway,
        "change_summary": change_summary,
        "compare_confidence": compare_confidence,
    }


def analyze_latest_filing_for_ticker(
    ticker: str,
    *,
    form_type: str = "10-K",
    user_agent: str | None = None,
    skill_dir: Path | None = None,
    cache_hours: float = 24.0,
    max_chars: int = 120_000,
    enable_llm: bool = True,
) -> dict[str, Any]:
    filings = fetch_recent_filings(
        ticker,
        form_type=form_type,
        limit=1,
        user_agent=user_agent,
    )
    if not filings:
        return {"ok": False, "error": f"No recent {form_type} filing found for {ticker.upper().strip()}."}
    doc = read_filing_document(
        filings[0],
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
    )
    return analyze_filing_document(doc, enable_llm=enable_llm)


def compare_ticker_vs_ticker(
    ticker_a: str,
    ticker_b: str,
    *,
    form_type: str = "10-K",
    user_agent: str | None = None,
    skill_dir: Path | None = None,
    cache_hours: float = 24.0,
    max_chars: int = 120_000,
    enable_llm: bool = True,
    highlight_changes_only: bool = False,
) -> dict[str, Any]:
    left = analyze_latest_filing_for_ticker(
        ticker_a,
        form_type=form_type,
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
        enable_llm=enable_llm,
    )
    right = analyze_latest_filing_for_ticker(
        ticker_b,
        form_type=form_type,
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
        enable_llm=enable_llm,
    )
    if not left.get("ok"):
        return {"ok": False, "error": left.get("error", "Left analysis failed")}
    if not right.get("ok"):
        return {"ok": False, "error": right.get("error", "Right analysis failed")}
    compare = compare_analyses(
        left,
        right,
        mode="ticker_vs_ticker",
        left_label=ticker_a.upper().strip(),
        right_label=ticker_b.upper().strip(),
        highlight_changes_only=highlight_changes_only,
    )
    return {
        "ok": True,
        "mode": "ticker_vs_ticker",
        "form_type": form_type.upper().strip(),
        "left": left,
        "right": right,
        "compare": compare,
    }


def compare_ticker_over_time(
    ticker: str,
    *,
    form_type: str = "10-K",
    user_agent: str | None = None,
    skill_dir: Path | None = None,
    cache_hours: float = 24.0,
    max_chars: int = 120_000,
    enable_llm: bool = True,
    highlight_changes_only: bool = False,
) -> dict[str, Any]:
    filings = fetch_recent_filings(
        ticker,
        form_type=form_type,
        limit=2,
        user_agent=user_agent,
    )
    if len(filings) < 2:
        return {"ok": False, "error": f"Need at least two recent {form_type} filings for {ticker.upper().strip()}."}
    latest_doc = read_filing_document(
        filings[0],
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
    )
    previous_doc = read_filing_document(
        filings[1],
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
    )
    latest = analyze_filing_document(latest_doc, enable_llm=enable_llm)
    previous = analyze_filing_document(previous_doc, enable_llm=enable_llm)
    if not latest.get("ok"):
        return {"ok": False, "error": latest.get("error", "Latest filing analysis failed")}
    if not previous.get("ok"):
        return {"ok": False, "error": previous.get("error", "Prior filing analysis failed")}
    compare = compare_analyses(
        latest,
        previous,
        mode="ticker_over_time",
        left_label=f"{ticker.upper().strip()} latest",
        right_label=f"{ticker.upper().strip()} prior",
        highlight_changes_only=highlight_changes_only,
    )
    return {
        "ok": True,
        "mode": "ticker_over_time",
        "form_type": form_type.upper().strip(),
        "latest": latest,
        "prior": previous,
        "compare": compare,
    }

