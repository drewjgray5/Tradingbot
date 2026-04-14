"""
SEC enrichment utilities with local caching.

Provides:
- ticker -> CIK lookup
- recent filing metadata (10-K, 10-Q, 8-K)
- lightweight risk/event tagging for downstream scoring and reporting
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
SEC_CACHE_FILE = ".sec_cache.json"
SEC_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSION_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TARGET_FORMS = {"10-K", "10-Q", "8-K"}
DEFAULT_USER_AGENT = "SchwabTradingBot contact@example.com"

HIGH_RISK_8K_TERMS = (
    "bankruptcy",
    "chapter 11",
    "going concern",
    "material weakness",
    "delisting",
    "default",
    "resignation",
    "restatement",
    "investigation",
    "litigation",
)


def _cache_path(skill_dir: Path | None = None) -> Path:
    return (skill_dir or SKILL_DIR) / SEC_CACHE_FILE


def _load_cache(skill_dir: Path | None = None) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    if not path.exists():
        return {"tickers": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("tickers"), dict):
            return data
    except Exception:
        pass
    return {"tickers": {}}


def _save_cache(cache: dict[str, Any], skill_dir: Path | None = None) -> None:
    path = _cache_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def _is_fresh(entry: dict[str, Any] | None, cache_hours: float) -> bool:
    if not entry:
        return False
    ts = float(entry.get("timestamp", 0) or 0)
    if ts <= 0:
        return False
    age_h = (time.time() - ts) / 3600.0
    return age_h <= float(cache_hours)


def _clamp_recent_filings(filings: list[dict[str, Any]], max_items: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in filings[:max_items]:
        out.append(
            {
                "form": str(f.get("form", "")),
                "date": str(f.get("date", "")),
                "description": str(f.get("description", ""))[:160],
                "url": str(f.get("url", "")),
            }
        )
    return out


def _risk_tag_from_filings(filings: list[dict[str, Any]]) -> tuple[str, list[str]]:
    # High risk if recent 8-K contains known risk terms.
    reasons: list[str] = []
    for f in filings:
        form = (f.get("form") or "").upper()
        desc = (f.get("description") or "").lower()
        if form == "8-K":
            for term in HIGH_RISK_8K_TERMS:
                if term in desc:
                    reasons.append(f"8-K keyword: {term}")
                    break
    if reasons:
        return "high", reasons
    if any((f.get("form") or "").upper() == "8-K" for f in filings):
        return "medium", ["recent 8-K present"]
    return "low", []


def _safe_user_agent(user_agent: str | None) -> str:
    ua = (user_agent or "").strip()
    if len(ua) < 12:
        return DEFAULT_USER_AGENT
    if "@" not in ua:
        return DEFAULT_USER_AGENT
    return ua


def fetch_sec_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    user_agent: str | None = None,
    cache_hours: float = 12.0,
    enabled: bool = True,
) -> dict[str, Any]:
    """
    Return SEC snapshot for a ticker.
    Response fields:
      ok, ticker, cik, recent_filings, risk_tag, risk_reasons, recent_8k,
      filing_recency_days, from_cache, error
    """
    tkr = ticker.upper().strip()
    if not enabled:
        return {
            "ok": False,
            "ticker": tkr,
            "cik": "",
            "recent_filings": [],
            "risk_tag": "unknown",
            "risk_reasons": [],
            "recent_8k": False,
            "filing_recency_days": None,
            "from_cache": False,
            "error": "SEC enrichment disabled",
        }

    cache = _load_cache(skill_dir)
    entry = (cache.get("tickers") or {}).get(tkr)
    if _is_fresh(entry, cache_hours):
        payload: dict[str, Any] = dict((entry or {}).get("payload") or {})
        payload["from_cache"] = True
        return payload

    import requests
    ua = _safe_user_agent(user_agent)
    payload = {
        "ok": False,
        "ticker": tkr,
        "cik": "",
        "recent_filings": [],
        "risk_tag": "unknown",
        "risk_reasons": [],
        "recent_8k": False,
        "filing_recency_days": None,
        "from_cache": False,
        "error": "",
    }

    try:
        idx = requests.get(SEC_INDEX_URL, headers={"User-Agent": ua}, timeout=20)
        idx.raise_for_status()
        tickers_data = idx.json()

        cik = None
        for v in tickers_data.values():
            if (v.get("ticker") or "").upper() == tkr:
                cik = str(v.get("cik_str", "")).zfill(10)
                break
        if not cik:
            payload["error"] = f"CIK not found for {tkr}"
            return payload
        payload["cik"] = cik

        sub = requests.get(SEC_SUBMISSION_URL.format(cik=cik), headers={"User-Agent": ua}, timeout=20)
        sub.raise_for_status()
        recent = (sub.json().get("filings", {}) or {}).get("recent", {}) or {}
        forms = recent.get("form", []) or []
        dates = recent.get("filingDate", []) or []
        accessions = recent.get("accessionNumber", []) or []
        docs = recent.get("primaryDocument", []) or []
        descriptions = recent.get("primaryDocDescription", []) or []

        filings: list[dict[str, Any]] = []
        for i in range(min(len(forms), 80)):
            form = forms[i]
            if form not in TARGET_FORMS:
                continue
            accession = str(accessions[i]).replace("-", "")
            doc = str(docs[i])
            filing_date = str(dates[i]) if i < len(dates) else ""
            desc = str(descriptions[i]) if i < len(descriptions) else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession}/{doc}"
            filings.append(
                {
                    "form": form,
                    "date": filing_date,
                    "description": desc,
                    "url": url,
                }
            )
            if len(filings) >= 8:
                break

        filings = _clamp_recent_filings(filings, max_items=6)
        payload["recent_filings"] = filings
        payload["recent_8k"] = any((f.get("form") or "").upper() == "8-K" for f in filings)
        payload["filing_recency_days"] = None
        if filings and filings[0].get("date"):
            from datetime import date

            try:
                d = date.fromisoformat(str(filings[0]["date"]))
                payload["filing_recency_days"] = (date.today() - d).days
            except Exception:
                payload["filing_recency_days"] = None

        risk_tag, reasons = _risk_tag_from_filings(filings)
        payload["risk_tag"] = risk_tag
        payload["risk_reasons"] = reasons[:3]
        payload["ok"] = True
        payload["error"] = ""
        return payload
    except Exception as e:
        payload["error"] = str(e)
        return payload
    finally:
        # Cache even failures briefly, to avoid hammering SEC on repeated failures.
        cache.setdefault("tickers", {})[tkr] = {"timestamp": time.time(), "payload": payload}
        try:
            _save_cache(cache, skill_dir)
        except Exception as e:
            LOG.debug("SEC cache write failed: %s", e)
