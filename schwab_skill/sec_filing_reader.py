"""
SEC filing reader with local accession-based caching.

This module fetches full filing text (normalized plain text) for 10-K/10-Q/8-K
documents from SEC EDGAR and caches parsed output by accession number.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
SEC_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSION_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/"
SEC_FILING_CACHE_FILE = ".sec_filing_cache.json"
DEFAULT_USER_AGENT = "SchwabTradingBot contact@example.com"


@dataclass
class FilingDocument:
    ticker: str
    cik: str
    form: str
    accession_number: str
    filing_date: str
    primary_document: str
    filing_url: str
    source: str
    text: str
    from_cache: bool


def _safe_user_agent(user_agent: str | None) -> str:
    ua = (user_agent or "").strip()
    if len(ua) < 12 or "@" not in ua:
        return DEFAULT_USER_AGENT
    return ua


def _cache_path(skill_dir: Path | None = None) -> Path:
    return (skill_dir or SKILL_DIR) / SEC_FILING_CACHE_FILE


def _load_cache(skill_dir: Path | None = None) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    if not path.exists():
        return {"filings": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"filings": {}}
    if not isinstance(data, dict):
        return {"filings": {}}
    if not isinstance(data.get("filings"), dict):
        data["filings"] = {}
    return data


def _save_cache(cache: dict[str, Any], skill_dir: Path | None = None) -> None:
    path = _cache_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _cache_key(cik: str, accession_number: str, primary_document: str) -> str:
    return f"{cik}:{accession_number}:{primary_document}".lower()


def _is_fresh(entry: dict[str, Any] | None, ttl_hours: float) -> bool:
    if not entry:
        return False
    ts = float(entry.get("timestamp", 0) or 0)
    if ts <= 0:
        return False
    age_h = (time.time() - ts) / 3600.0
    return age_h <= float(ttl_hours)


def _request_json(url: str, *, user_agent: str, timeout_seconds: int = 20, retries: int = 2) -> dict[str, Any]:
    headers = {"User-Agent": user_agent}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout_seconds)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("SEC response was not a JSON object")
            return data
        except Exception as exc:  # pragma: no cover - exercised by runtime failures
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"SEC request failed for {url}: {last_error}")


def _request_text(url: str, *, user_agent: str, timeout_seconds: int = 30, retries: int = 2) -> str:
    headers = {"User-Agent": user_agent}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout_seconds)
            resp.raise_for_status()
            return resp.text or ""
        except Exception as exc:  # pragma: no cover - exercised by runtime failures
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"SEC filing fetch failed for {url}: {last_error}")


def _normalize_text(raw: str) -> str:
    txt = raw or ""
    txt = re.sub(r"(?is)<script.*?>.*?</script>", " ", txt)
    txt = re.sub(r"(?is)<style.*?>.*?</style>", " ", txt)
    txt = re.sub(r"(?is)<table.*?>.*?</table>", " ", txt)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = unescape(txt)
    txt = txt.replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[^\S\n]{2,}", " ", txt)
    return txt.strip()


def _clip_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def resolve_cik_for_ticker(ticker: str, *, user_agent: str) -> str:
    tkr = ticker.upper().strip()
    idx = _request_json(SEC_INDEX_URL, user_agent=user_agent)
    for item in idx.values():
        if (item.get("ticker") or "").upper() == tkr:
            return str(item.get("cik_str", "")).zfill(10)
    raise ValueError(f"CIK not found for {tkr}")


def fetch_recent_filings(
    ticker: str,
    *,
    form_type: str = "10-K",
    limit: int = 5,
    user_agent: str | None = None,
) -> list[dict[str, str]]:
    ua = _safe_user_agent(user_agent)
    cik = resolve_cik_for_ticker(ticker, user_agent=ua)
    sub = _request_json(SEC_SUBMISSION_URL.format(cik=cik), user_agent=ua)
    recent = (sub.get("filings", {}) or {}).get("recent", {}) or {}
    forms = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accessions = recent.get("accessionNumber", []) or []
    docs = recent.get("primaryDocument", []) or []
    descriptions = recent.get("primaryDocDescription", []) or []

    target = form_type.upper().strip()
    out: list[dict[str, str]] = []
    for i in range(min(len(forms), 200)):
        form = str(forms[i] or "").upper()
        if target and form != target:
            continue
        accession_with_dash = str(accessions[i] or "")
        accession = accession_with_dash.replace("-", "")
        primary_document = str(docs[i] or "")
        filing_date = str(dates[i] or "")
        description = str(descriptions[i] or "")
        if not accession or not primary_document:
            continue
        url = urljoin(
            SEC_ARCHIVES_BASE,
            f"{cik.lstrip('0')}/{accession}/{primary_document}",
        )
        out.append(
            {
                "ticker": ticker.upper().strip(),
                "cik": cik,
                "form": form,
                "filing_date": filing_date,
                "description": description[:220],
                "accession_number": accession,
                "accession_number_raw": accession_with_dash,
                "primary_document": primary_document,
                "filing_url": url,
            }
        )
        if len(out) >= max(1, int(limit)):
            break
    return out


def read_filing_document(
    filing: dict[str, str],
    *,
    user_agent: str | None = None,
    skill_dir: Path | None = None,
    cache_hours: float = 24.0,
    max_chars: int = 120_000,
) -> FilingDocument:
    ua = _safe_user_agent(user_agent)
    ticker = str(filing.get("ticker", "")).upper().strip()
    cik = str(filing.get("cik", "")).zfill(10)
    accession = str(filing.get("accession_number", ""))
    primary_document = str(filing.get("primary_document", ""))
    form = str(filing.get("form", ""))
    filing_date = str(filing.get("filing_date", ""))
    filing_url = str(filing.get("filing_url", ""))
    cache = _load_cache(skill_dir)
    key = _cache_key(cik, accession, primary_document)
    cached = (cache.get("filings") or {}).get(key)
    if _is_fresh(cached, cache_hours):
        payload = cached.get("payload") or {}
        return FilingDocument(
            ticker=ticker,
            cik=cik,
            form=form,
            accession_number=accession,
            filing_date=filing_date,
            primary_document=primary_document,
            filing_url=filing_url,
            source="cache",
            text=str(payload.get("text", "")),
            from_cache=True,
        )

    if not filing_url:
        raise ValueError("Missing filing URL")
    raw_text = _request_text(filing_url, user_agent=ua, timeout_seconds=35)
    normalized = _clip_text(_normalize_text(raw_text), max_chars=max_chars)
    doc = FilingDocument(
        ticker=ticker,
        cik=cik,
        form=form,
        accession_number=accession,
        filing_date=filing_date,
        primary_document=primary_document,
        filing_url=filing_url,
        source="sec",
        text=normalized,
        from_cache=False,
    )
    cache.setdefault("filings", {})[key] = {
        "timestamp": time.time(),
        "payload": {
            "text": doc.text,
        },
    }
    try:
        _save_cache(cache, skill_dir)
    except Exception as exc:
        LOG.debug("SEC filing cache write failed: %s", exc)
    return doc


def read_latest_and_previous_filing(
    ticker: str,
    *,
    form_type: str = "10-K",
    user_agent: str | None = None,
    skill_dir: Path | None = None,
    cache_hours: float = 24.0,
    max_chars: int = 120_000,
) -> tuple[FilingDocument | None, FilingDocument | None]:
    filings = fetch_recent_filings(
        ticker,
        form_type=form_type,
        limit=2,
        user_agent=user_agent,
    )
    latest = read_filing_document(
        filings[0],
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
    ) if filings else None
    previous = read_filing_document(
        filings[1],
        user_agent=user_agent,
        skill_dir=skill_dir,
        cache_hours=cache_hours,
        max_chars=max_chars,
    ) if len(filings) > 1 else None
    return latest, previous

