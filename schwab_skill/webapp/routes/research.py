"""Research routes: SEC analysis, full reports, chart data, decision card."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from full_report import REPORT_SECTION_MAP, generate_full_report, quick_check, report_to_json
from schwab_auth import DualSchwabAuth
from sec_filing_compare import (
    analyze_latest_filing_for_ticker,
    compare_ticker_over_time,
    compare_ticker_vs_ticker,
)

from ..schemas import ApiResponse

router = APIRouter(tags=["research"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err_response(endpoint: str, exc: Exception) -> ApiResponse:
    from ..recovery_map import map_failure

    mapped = map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


def _sec_analysis_settings() -> dict[str, Any]:
    from config import (
        get_edgar_user_agent,
        get_sec_filing_analysis_enabled,
        get_sec_filing_cache_hours,
        get_sec_filing_compare_enabled,
        get_sec_filing_llm_summary_enabled,
        get_sec_filing_max_chars,
        get_sec_filing_max_compare_items,
    )

    return {
        "analysis_enabled": bool(get_sec_filing_analysis_enabled(SKILL_DIR)),
        "compare_enabled": bool(get_sec_filing_compare_enabled(SKILL_DIR)),
        "user_agent": get_edgar_user_agent(SKILL_DIR),
        "cache_hours": float(get_sec_filing_cache_hours(SKILL_DIR)),
        "max_chars": int(get_sec_filing_max_chars(SKILL_DIR)),
        "max_compare_items": int(get_sec_filing_max_compare_items(SKILL_DIR)),
        "llm_enabled": bool(get_sec_filing_llm_summary_enabled(SKILL_DIR)),
    }


def _normalize_sec_analysis_payload(payload: dict[str, Any], *, analysis_mode: str = "full_text") -> dict[str, Any]:
    data = dict(payload or {})
    confidence = int(data.get("confidence", 0) or 0)
    why = list(data.get("why") or [])
    limits = list(data.get("limits") or [])
    evidence = list(data.get("evidence") or [])
    summary_headline = str(data.get("summary_headline") or "").strip()
    if not summary_headline:
        verdict = str(data.get("verdict") or "neutral")
        summary_headline = (
            f"{data.get('ticker', '')} {data.get('form', '')} filing reads {verdict} "
            f"with confidence {confidence}/100."
        ).strip()
    narrative_summary = str(data.get("narrative_summary") or "").strip()
    if not narrative_summary:
        narrative_summary = " ".join(why[:2]).strip() or str(data.get("high_level_takeaway") or "").strip()
    data["summary_headline"] = summary_headline
    data["narrative_summary"] = narrative_summary
    data["confidence"] = confidence
    data["limits"] = limits
    data["evidence"] = evidence
    data["analysis_mode"] = analysis_mode
    data["data_freshness"] = {
        "from_cache": bool(data.get("from_cache", False)),
        "source": str(data.get("source") or ""),
    }
    return data


def _normalize_sec_compare_payload(payload: dict[str, Any], *, analysis_mode: str = "full_text") -> dict[str, Any]:
    data = dict(payload or {})
    compare_data = dict(data.get("compare") or {})
    similarities = compare_data.get("similarities") or []
    differences = compare_data.get("differences") or []
    investor_takeaway = str(compare_data.get("investor_takeaway") or "").strip()
    compare_data.setdefault(
        "summary_headline",
        "SEC compare completed with meaningful differences." if differences else "SEC compare completed with broad alignment.",
    )
    compare_data.setdefault(
        "narrative_summary",
        (
            f"{investor_takeaway} "
            f"Shared signal: {(similarities[0] if similarities else 'limited overlap noted.')} "
            f"Key difference: {(differences[0] if differences else 'no major contrast highlighted.')}."
        ).strip(),
    )
    compare_data.setdefault("top_differences", differences[:3])
    compare_data.setdefault("top_commonalities", similarities[:3])
    if "change_summary" not in compare_data:
        compare_data["change_summary"] = {
            "new_risks": [],
            "resolved_risks": [],
            "guidance_shift": "unchanged",
            "evidence_ranked": [],
            "plain_english_rationale": [],
        }
    compare_data["analysis_mode"] = analysis_mode
    compare_data.setdefault("compare_confidence", 0)
    compare_data.setdefault("limits", [])
    compare_data.setdefault("evidence", compare_data.get("change_summary", {}).get("evidence_ranked", []))
    left = data.get("left") or data.get("latest") or {}
    right = data.get("right") or data.get("prior") or {}
    compare_data["data_freshness"] = {
        "left_from_cache": bool((left or {}).get("from_cache", False)),
        "right_from_cache": bool((right or {}).get("from_cache", False)),
        "left_source": str((left or {}).get("source") or ""),
        "right_source": str((right or {}).get("source") or ""),
    }
    data["compare"] = compare_data
    return data


def _build_report_verdicts(report: dict[str, Any]) -> dict[str, Any]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    miro = report.get("mirofish") or {}
    signal_score = float(technical.get("signal_score", 0) or 0)
    mos = float(dcf.get("margin_of_safety", 0) or 0)
    health_flags = health.get("flags") or []
    conviction = float(miro.get("conviction_score", 0) or 0)

    def bucket(score: float, high: float, low: float) -> str:
        if score >= high:
            return "bullish"
        if score <= low:
            return "bearish"
        return "neutral"

    return {
        "technical": {
            "verdict": bucket(signal_score, 65.0, 45.0),
            "takeaway": "Trend setup aligned." if technical.get("stage_2") and technical.get("vcp") else "Setup quality is mixed.",
        },
        "dcf": {
            "verdict": bucket(mos, 10.0, -10.0),
            "takeaway": "Valuation supports upside." if mos >= 0 else "Valuation indicates premium pricing.",
        },
        "health": {
            "verdict": "bullish" if len(health_flags) == 0 else ("bearish" if len(health_flags) >= 3 else "neutral"),
            "takeaway": "Balance sheet and margins are stable." if len(health_flags) == 0 else "Review flagged financial risks.",
        },
        "mirofish": {
            "verdict": bucket(conviction, 30.0, -30.0),
            "takeaway": (miro.get("summary") or "No sentiment synthesis available.")[:220],
        },
    }


@router.get("/api/chart/{ticker}", response_model=ApiResponse)
def chart_data(ticker: str, days: int = 120) -> ApiResponse:
    """OHLCV candle data for Lightweight Charts."""
    try:
        from market_data import get_price_history

        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        df = get_price_history(
            ticker.upper().strip(),
            period_days=min(365, max(30, days)),
            auth=auth,
            skill_dir=SKILL_DIR,
        )
        if df is None or df.empty:
            return ApiResponse(ok=False, error=f"No price data for {ticker}")

        candles: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            ts = row.get("datetime") or row.get("date") or row.name
            try:
                if hasattr(ts, "timestamp"):
                    epoch = int(ts.timestamp())
                else:
                    from datetime import datetime as _dt
                    epoch = int(_dt.fromisoformat(str(ts)).timestamp())
            except Exception:
                continue
            candles.append({
                "time": epoch,
                "open": round(float(row.get("open", 0)), 2),
                "high": round(float(row.get("high", 0)), 2),
                "low": round(float(row.get("low", 0)), 2),
                "close": round(float(row.get("close", 0)), 2),
                "volume": int(row.get("volume", 0) or 0),
            })
        candles.sort(key=lambda c: c["time"])
        return _ok({"ticker": ticker.upper().strip(), "candles": candles})
    except Exception as e:
        return _err_response("chart_data", e)


@router.get("/api/check/{ticker}", response_model=ApiResponse)
def check_ticker(ticker: str) -> ApiResponse:
    try:
        data = quick_check(ticker.upper().strip())
        return _ok(data)
    except Exception as e:
        return _err_response("check", e)


@router.get("/api/report/{ticker}", response_model=ApiResponse)
def report_ticker(
    ticker: str,
    section: str | None = None,
    skip_mirofish: bool = False,
    skip_edgar: bool = False,
) -> ApiResponse:
    try:
        section_key = None
        if section:
            section_key = REPORT_SECTION_MAP.get(section.lower().strip())
            if not section_key:
                return ApiResponse(ok=False, error=f"Invalid section '{section}'. Use: tech, dcf, comps, health, edgar, mirofish.")

        report = generate_full_report(
            ticker=ticker.upper().strip(),
            skip_mirofish=skip_mirofish,
            skip_edgar=skip_edgar,
        )
        data = json.loads(report_to_json(report))
        section_verdicts = _build_report_verdicts(data)
        if section_key:
            section_data = data.get(section_key)
            return _ok({
                "ticker": data.get("ticker"),
                "generated_at": data.get("generated_at"),
                "section": section_key,
                "data": section_data,
                "section_verdicts": section_verdicts,
                "section_quick_verdict": section_verdicts.get(section_key, {}),
            })
        data["section_verdicts"] = section_verdicts
        return _ok(data)
    except Exception as e:
        return _err_response("report", e)


@router.get("/api/sec/analyze/{ticker}", response_model=ApiResponse)
def sec_analyze_ticker(ticker: str, form_type: str = "10-K") -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        out = analyze_latest_filing_for_ticker(
            ticker=ticker.upper().strip(),
            form_type=form_type.upper().strip(),
            user_agent=cfg["user_agent"],
            skill_dir=SKILL_DIR,
            cache_hours=cfg["cache_hours"],
            max_chars=cfg["max_chars"],
            enable_llm=cfg["llm_enabled"],
        )
        if not out.get("ok"):
            return ApiResponse(ok=False, error=str(out.get("error", "SEC analysis failed")))
        return _ok(_normalize_sec_analysis_payload(out))
    except Exception as e:
        return _err_response("sec_analyze", e)


@router.get("/sec/analyze/{ticker}", response_model=ApiResponse)
def sec_analyze_ticker_alias(ticker: str, form_type: str = "10-K") -> ApiResponse:
    return sec_analyze_ticker(ticker=ticker, form_type=form_type)


@router.get("/api/sec/compare", response_model=ApiResponse)
def sec_compare(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
) -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        if not cfg["compare_enabled"]:
            return ApiResponse(ok=False, error="SEC filing compare is disabled by configuration.")
        safe_mode = mode.strip().lower()
        safe_form = form_type.upper().strip()
        safe_ticker = ticker.upper().strip()
        safe_ticker_b = ticker_b.upper().strip()
        if cfg["max_compare_items"] < 2:
            return ApiResponse(ok=False, error="SEC compare limit is below required minimum.")

        if safe_mode == "ticker_vs_ticker":
            if not safe_ticker or not safe_ticker_b:
                return ApiResponse(ok=False, error="ticker and ticker_b are required for ticker_vs_ticker mode.")
            out = compare_ticker_vs_ticker(
                safe_ticker, safe_ticker_b,
                form_type=safe_form, user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR, cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"], enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        elif safe_mode == "ticker_over_time":
            if not safe_ticker:
                return ApiResponse(ok=False, error="ticker is required for ticker_over_time mode.")
            out = compare_ticker_over_time(
                safe_ticker,
                form_type=safe_form, user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR, cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"], enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        else:
            return ApiResponse(ok=False, error="Invalid mode. Use ticker_vs_ticker or ticker_over_time.")

        if not out.get("ok"):
            return ApiResponse(ok=False, error=str(out.get("error", "SEC compare failed")))
        return _ok(_normalize_sec_compare_payload(out))
    except Exception as e:
        return _err_response("sec_compare", e)


@router.get("/sec/compare", response_model=ApiResponse)
def sec_compare_alias(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
) -> ApiResponse:
    return sec_compare(
        mode=mode, ticker=ticker, ticker_b=ticker_b,
        form_type=form_type, highlight_changes_only=highlight_changes_only,
    )
