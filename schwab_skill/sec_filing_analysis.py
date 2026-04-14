"""
High-level filing analysis utilities.

The analyzer combines deterministic extraction (for explainability) with an
optional LLM summarization pass (for concise narrative output).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from sec_filing_reader import FilingDocument

LOG = logging.getLogger(__name__)

KEY_TERMS = {
    "risk": (
        "material weakness",
        "going concern",
        "litigation",
        "investigation",
        "default",
        "bankruptcy",
        "impairment",
        "restatement",
    ),
    "guidance_up": ("raised guidance", "increase outlook", "reaffirmed guidance", "improved outlook"),
    "guidance_down": ("lowered guidance", "withdraw guidance", "reduced outlook", "headwind"),
    "liquidity": ("cash and cash equivalents", "liquidity", "credit facility", "revolver"),
    "debt": ("long-term debt", "debt", "leverage", "interest expense"),
    "r_and_d": ("research and development", "r&d", "product development", "engineering expense"),
}


def _normalize_spaces(text: str) -> str:
    txt = text or ""
    txt = txt.replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _extract_numbers_near_keywords(text: str, keywords: tuple[str, ...], max_hits: int = 6) -> list[str]:
    lowered = text.lower()
    out: list[str] = []
    number_pattern = re.compile(r"[$]?\d[\d,]*(?:\.\d+)?\s*(?:million|billion|thousand|%)?", re.IGNORECASE)
    for kw in keywords:
        start = 0
        while len(out) < max_hits:
            idx = lowered.find(kw, start)
            if idx < 0:
                break
            window_start = max(0, idx - 120)
            window_end = min(len(text), idx + 220)
            window = text[window_start:window_end]
            found = number_pattern.findall(window)
            if found:
                sample = ", ".join(found[:2])
                out.append(f"{kw}: {sample}")
            start = idx + len(kw)
    return out[:max_hits]


def _extract_risk_terms(text: str, max_items: int = 8) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for term in KEY_TERMS["risk"]:
        if term in lowered:
            hits.append(term)
    return hits[:max_items]


def _extract_theme_sentences(text: str, max_sentences: int = 8) -> list[str]:
    if not text:
        return []
    sentences = re.split(r"(?<=[\.\!\?])\s+", text)
    score_terms = (
        "revenue",
        "margin",
        "guidance",
        "demand",
        "liquidity",
        "debt",
        "cash",
        "risk",
        "outlook",
    )
    scored: list[tuple[int, str]] = []
    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 60 or len(s) > 260:
            continue
        lowered = s.lower()
        score = sum(1 for t in score_terms if t in lowered)
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    uniq: list[str] = []
    for _, sentence in scored:
        if sentence not in uniq:
            uniq.append(sentence)
        if len(uniq) >= max_sentences:
            break
    return uniq


def _extract_guidance_signal(text: str) -> str:
    lowered = text.lower()
    up = sum(1 for t in KEY_TERMS["guidance_up"] if t in lowered)
    down = sum(1 for t in KEY_TERMS["guidance_down"] if t in lowered)
    if up > down and up > 0:
        return "positive"
    if down > up and down > 0:
        return "negative"
    if up == down and up > 0:
        return "mixed"
    return "neutral"


def _extract_evidence_snippets(
    text: str,
    *,
    guidance_signal: str,
    risk_terms: list[str],
    max_items: int = 8,
) -> list[dict[str, str]]:
    if not text:
        return []
    sentences = re.split(r"(?<=[\.\!\?])\s+", text)
    evidence: list[dict[str, str]] = []

    guidance_terms = tuple(KEY_TERMS["guidance_up"] + KEY_TERMS["guidance_down"])
    watch_terms = (
        "liquidity",
        "credit facility",
        "revolver",
        "cash and cash equivalents",
        "long-term debt",
        "interest expense",
    )

    def _append_if_match(sentence: str, claim: str, terms: tuple[str, ...], section_hint: str) -> None:
        lowered = sentence.lower()
        for term in terms:
            if term in lowered:
                evidence.append(
                    {
                        "claim": claim,
                        "term": term,
                        "quote": sentence[:240],
                        "section_hint": section_hint,
                    }
                )
                return

    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 55:
            continue
        _append_if_match(
            s,
            claim=f"Guidance signal appears {guidance_signal}",
            terms=guidance_terms,
            section_hint="outlook",
        )
        if risk_terms:
            _append_if_match(
                s,
                claim="Risk language appears in current filing",
                terms=tuple(risk_terms),
                section_hint="risk_factors",
            )
        _append_if_match(
            s,
            claim="Balance sheet or funding language referenced",
            terms=watch_terms,
            section_hint="liquidity_and_capital",
        )
        if len(evidence) >= max_items:
            break
    return evidence[:max_items]


def _derive_verdict(guidance_signal: str, risk_terms: list[str], evidence_count: int) -> str:
    guidance = (guidance_signal or "neutral").lower()
    risk_count = len(risk_terms)
    if guidance == "positive" and risk_count <= 1 and evidence_count >= 2:
        return "bullish"
    if guidance == "negative" and risk_count >= 2:
        return "bearish"
    if risk_count >= 4:
        return "bearish"
    return "neutral"


def _derive_why(
    *,
    guidance_signal: str,
    risk_terms: list[str],
    evidence_count: int,
    coverage_ratio: float,
) -> list[str]:
    reasons: list[str] = []
    reasons.append(f"Guidance language is {guidance_signal}.")
    reasons.append(f"Detected {len(risk_terms)} notable risk terms.")
    reasons.append(f"Extracted {evidence_count} supporting evidence snippets.")
    if coverage_ratio < 0.15:
        reasons.append("Coverage of analyzed text is limited; confidence is reduced.")
    return reasons[:4]


def _derive_limits(*, coverage_ratio: float, char_count: int, evidence_count: int) -> list[str]:
    limits: list[str] = []
    if coverage_ratio < 0.15:
        limits.append("Low excerpt coverage ratio")
    if char_count < 5000:
        limits.append("Short normalized filing text")
    if evidence_count == 0:
        limits.append("No direct quote evidence captured")
    return limits


def _estimate_confidence(*, coverage_ratio: float, evidence_count: int, limits: list[str]) -> int:
    score = 45.0
    score += min(25.0, coverage_ratio * 100.0 * 0.25)
    score += min(20.0, float(evidence_count) * 2.5)
    score -= float(len(limits)) * 8.0
    return int(max(5.0, min(95.0, score)))


def _derive_takeaway(analysis: dict[str, Any]) -> str:
    risk_terms = analysis.get("risk_terms") or []
    guidance = analysis.get("guidance_signal", "neutral")
    coverage = analysis.get("coverage_ratio", 0.0) or 0.0
    if risk_terms and guidance == "negative":
        return "Risk disclosures and guidance language both lean cautious."
    if risk_terms and guidance in {"neutral", "mixed"}:
        return "Risk flags are present, but forward guidance is not uniformly negative."
    if not risk_terms and guidance == "positive":
        return "Guidance language appears constructive with limited explicit risk wording."
    if coverage < 0.15:
        return "Only a small portion of filing text was analyzed; confidence is limited."
    return "Filing tone is mixed; review key deltas and metrics for conviction."


def _call_llm_summary(prompt: str, max_tokens: int = 260) -> str:
    api_key = os.environ.get("MIROFISH_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return ""
    base_url = (os.environ.get("LLM_BASE_URL") or "").strip()
    model = os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You summarize SEC filings for retail investors. Keep output high-level, factual, and concise. "
                        "Return 3-5 bullets only, no markdown headers."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # pragma: no cover - runtime dependent
        LOG.debug("LLM summary failed: %s", exc)
        return ""


def analyze_filing_document(
    doc: FilingDocument,
    *,
    enable_llm: bool = True,
    max_theme_sentences: int = 8,
) -> dict[str, Any]:
    text = _normalize_spaces(doc.text or "")
    char_count = len(text)
    if char_count == 0:
        return {
            "ok": False,
            "error": "Filing text is empty",
            "ticker": doc.ticker,
            "form": doc.form,
            "filing_date": doc.filing_date,
            "accession_number": doc.accession_number,
        }

    theme_sentences = _extract_theme_sentences(text, max_sentences=max_theme_sentences)
    risk_terms = _extract_risk_terms(text)
    guidance_signal = _extract_guidance_signal(text)
    revenue_mentions = _extract_numbers_near_keywords(
        text,
        keywords=("revenue", "net sales", "sales"),
        max_hits=6,
    )
    profit_mentions = _extract_numbers_near_keywords(
        text,
        keywords=("net income", "operating income", "earnings per share", "eps"),
        max_hits=6,
    )
    cashflow_mentions = _extract_numbers_near_keywords(
        text,
        keywords=("operating cash flow", "free cash flow", "cash provided by operations"),
        max_hits=6,
    )
    debt_mentions = _extract_numbers_near_keywords(
        text,
        keywords=("debt", "long-term debt", "interest expense"),
        max_hits=6,
    )
    r_and_d_mentions = _extract_numbers_near_keywords(
        text,
        keywords=KEY_TERMS["r_and_d"],
        max_hits=6,
    )
    liquidity_mentions = _extract_numbers_near_keywords(
        text,
        keywords=("cash and cash equivalents", "liquidity", "credit facility"),
        max_hits=6,
    )

    deterministic_summary = [
        f"Guidance tone: {guidance_signal}.",
        f"Risk term hits: {len(risk_terms)}.",
        f"Revenue references captured: {len(revenue_mentions)}.",
        f"Profitability references captured: {len(profit_mentions)}.",
    ]
    deterministic_summary.extend([f"Theme: {s}" for s in theme_sentences[:3]])

    coverage_chars = sum(len(s) for s in theme_sentences[:max_theme_sentences])
    coverage_ratio = min(1.0, coverage_chars / max(1, char_count))
    evidence = _extract_evidence_snippets(
        text,
        guidance_signal=guidance_signal,
        risk_terms=risk_terms,
        max_items=8,
    )
    limits = _derive_limits(
        coverage_ratio=coverage_ratio,
        char_count=char_count,
        evidence_count=len(evidence),
    )
    verdict = _derive_verdict(guidance_signal, risk_terms, len(evidence))
    why = _derive_why(
        guidance_signal=guidance_signal,
        risk_terms=risk_terms,
        evidence_count=len(evidence),
        coverage_ratio=coverage_ratio,
    )
    confidence = _estimate_confidence(
        coverage_ratio=coverage_ratio,
        evidence_count=len(evidence),
        limits=limits,
    )

    llm_summary = ""
    if enable_llm:
        compact_context = "\n".join(theme_sentences[:6])
        prompt = (
            f"Ticker: {doc.ticker}\n"
            f"Form: {doc.form}\n"
            f"Filing date: {doc.filing_date}\n"
            f"Guidance signal: {guidance_signal}\n"
            f"Risk terms: {', '.join(risk_terms[:8]) or 'none'}\n"
            f"Revenue refs: {revenue_mentions[:3]}\n"
            f"Profit refs: {profit_mentions[:3]}\n"
            f"Cash flow refs: {cashflow_mentions[:3]}\n"
            f"Debt refs: {debt_mentions[:3]}\n"
            f"Liquidity refs: {liquidity_mentions[:3]}\n\n"
            f"Important filing excerpts:\n{compact_context}"
        )
        llm_summary = _call_llm_summary(prompt)

    analysis: dict[str, Any] = {
        "ok": True,
        "ticker": doc.ticker,
        "form": doc.form,
        "filing_date": doc.filing_date,
        "accession_number": doc.accession_number,
        "filing_url": doc.filing_url,
        "source": doc.source,
        "from_cache": doc.from_cache,
        "coverage_ratio": round(float(coverage_ratio), 4),
        "char_count": char_count,
        "verdict": verdict,
        "confidence": confidence,
        "why": why,
        "evidence": evidence,
        "limits": limits,
        "guidance_signal": guidance_signal,
        "risk_terms": risk_terms,
        "key_themes": theme_sentences[:max_theme_sentences],
        "kpi_signals": {
            "revenue_mentions": revenue_mentions,
            "profit_mentions": profit_mentions,
            "cashflow_mentions": cashflow_mentions,
            "debt_mentions": debt_mentions,
            "r_and_d_mentions": r_and_d_mentions,
            "liquidity_mentions": liquidity_mentions,
        },
        "summary_bullets": deterministic_summary[:8],
        "llm_summary": llm_summary,
    }
    analysis["high_level_takeaway"] = _derive_takeaway(analysis)
    analysis["analyst_summary"] = " ".join(why[:2]).strip()
    analysis["summary_headline"] = (
        f"{doc.ticker} {doc.form} looks {verdict} with confidence {confidence}/100."
    )
    analysis["narrative_summary"] = (
        f"{analysis['analyst_summary']} {analysis['high_level_takeaway']}".strip()
    )
    return analysis

