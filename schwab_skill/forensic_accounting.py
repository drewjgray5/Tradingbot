"""
Forensic accounting filters with local caching.

Provides:
- Sloan Ratio
- Beneish M-Score
- Altman Z-Score
- Unified snapshot and forensic flags
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
FORENSIC_CACHE_FILE = ".forensic_cache.json"


def _safe_get_col_value(df: Any, col_idx: int, label: str) -> float | None:
    try:
        val = df.iloc[:, col_idx].loc[label]
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def _safe_div(numer: float | None, denom: float | None) -> float | None:
    if numer is None or denom is None:
        return None
    if abs(float(denom)) < 1e-12:
        return None
    return float(numer) / float(denom)


def _normalize_symbol(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def compute_sloan_ratio(ticker: str) -> dict[str, Any] | None:
    """
    Sloan accrual ratio:
      (net_income - operating_cash_flow) / total_assets
    """
    try:
        import yfinance as yf

        tkr = _normalize_symbol(ticker)
        t = yf.Ticker(tkr)
        fin = t.quarterly_financials
        cf = t.quarterly_cashflow
        bs = t.quarterly_balance_sheet
        if fin is None or fin.empty or cf is None or cf.empty or bs is None or bs.empty:
            return None

        net_income = _safe_get_col_value(fin, 0, "Net Income")
        total_assets = _safe_get_col_value(bs, 0, "Total Assets")

        ocf = None
        for label in (
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
            "Total Cash From Operating Activities",
        ):
            ocf = _safe_get_col_value(cf, 0, label)
            if ocf is not None:
                break

        ratio = _safe_div((net_income - ocf) if net_income is not None and ocf is not None else None, total_assets)
        if ratio is None:
            return None

        return {
            "sloan_ratio": float(ratio),
            "net_income": float(net_income or 0.0),
            "ocf": float(ocf or 0.0),
            "total_assets": float(total_assets or 0.0),
        }
    except Exception as exc:
        LOG.debug("Sloan ratio failed for %s: %s", ticker, exc)
        return None


def compute_beneish_m_score(ticker: str) -> dict[str, Any] | None:
    """
    Beneish M-Score:
      M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
          + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI
    """
    try:
        import yfinance as yf

        tkr = _normalize_symbol(ticker)
        t = yf.Ticker(tkr)
        fin = t.financials
        bs = t.balance_sheet
        cf = t.cashflow
        if fin is None or fin.empty or bs is None or bs.empty:
            return None
        if fin.shape[1] < 2 or bs.shape[1] < 2:
            return None

        rev_c = _safe_get_col_value(fin, 0, "Total Revenue")
        rev_p = _safe_get_col_value(fin, 1, "Total Revenue")
        cogs_c = _safe_get_col_value(fin, 0, "Cost Of Revenue")
        cogs_p = _safe_get_col_value(fin, 1, "Cost Of Revenue")

        recv_c = _safe_get_col_value(bs, 0, "Net Receivables")
        if recv_c is None:
            recv_c = _safe_get_col_value(bs, 0, "Receivables")
        recv_p = _safe_get_col_value(bs, 1, "Net Receivables")
        if recv_p is None:
            recv_p = _safe_get_col_value(bs, 1, "Receivables")

        ta_c = _safe_get_col_value(bs, 0, "Total Assets")
        ta_p = _safe_get_col_value(bs, 1, "Total Assets")
        ca_c = _safe_get_col_value(bs, 0, "Current Assets")
        ca_p = _safe_get_col_value(bs, 1, "Current Assets")

        ppe_c = _safe_get_col_value(bs, 0, "Net PPE")
        if ppe_c is None:
            ppe_c = _safe_get_col_value(bs, 0, "Property Plant Equipment Net")
        ppe_p = _safe_get_col_value(bs, 1, "Net PPE")
        if ppe_p is None:
            ppe_p = _safe_get_col_value(bs, 1, "Property Plant Equipment Net")

        dep_c = _safe_get_col_value(fin, 0, "Depreciation And Amortization In Income Statement")
        if dep_c is None:
            dep_c = _safe_get_col_value(fin, 0, "Reconciled Depreciation")
        dep_p = _safe_get_col_value(fin, 1, "Depreciation And Amortization In Income Statement")
        if dep_p is None:
            dep_p = _safe_get_col_value(fin, 1, "Reconciled Depreciation")

        sga_c = _safe_get_col_value(fin, 0, "Selling General And Administration")
        sga_p = _safe_get_col_value(fin, 1, "Selling General And Administration")
        ni_c = _safe_get_col_value(fin, 0, "Net Income")

        ltd_c = _safe_get_col_value(bs, 0, "Long Term Debt")
        ltd_p = _safe_get_col_value(bs, 1, "Long Term Debt")
        cl_c = _safe_get_col_value(bs, 0, "Current Liabilities")
        if cl_c is None:
            cl_c = _safe_get_col_value(bs, 0, "Total Current Liabilities")
        cl_p = _safe_get_col_value(bs, 1, "Current Liabilities")
        if cl_p is None:
            cl_p = _safe_get_col_value(bs, 1, "Total Current Liabilities")

        ocf_c = None
        if cf is not None and not cf.empty:
            for label in (
                "Operating Cash Flow",
                "Cash Flow From Continuing Operating Activities",
                "Total Cash From Operating Activities",
            ):
                ocf_c = _safe_get_col_value(cf, 0, label)
                if ocf_c is not None:
                    break

        dsri = _safe_div(_safe_div(recv_c, rev_c), _safe_div(recv_p, rev_p))
        gm_c = _safe_div((rev_c - cogs_c) if rev_c is not None and cogs_c is not None else None, rev_c)
        gm_p = _safe_div((rev_p - cogs_p) if rev_p is not None and cogs_p is not None else None, rev_p)
        gmi = _safe_div(gm_p, gm_c)

        aq_c = _safe_div(
            (ta_c - ca_c - ppe_c) if ta_c is not None and ca_c is not None and ppe_c is not None else None,
            ta_c,
        )
        aq_p = _safe_div(
            (ta_p - ca_p - ppe_p) if ta_p is not None and ca_p is not None and ppe_p is not None else None,
            ta_p,
        )
        aqi = _safe_div(aq_c, aq_p)
        sgi = _safe_div(rev_c, rev_p)

        dep_rate_c = _safe_div(dep_c, (dep_c + ppe_c) if dep_c is not None and ppe_c is not None else None)
        dep_rate_p = _safe_div(dep_p, (dep_p + ppe_p) if dep_p is not None and ppe_p is not None else None)
        depi = _safe_div(dep_rate_p, dep_rate_c)

        sgai = _safe_div(_safe_div(sga_c, rev_c), _safe_div(sga_p, rev_p))
        lev_c = _safe_div((ltd_c or 0.0) + (cl_c or 0.0), ta_c)
        lev_p = _safe_div((ltd_p or 0.0) + (cl_p or 0.0), ta_p)
        lvgi = _safe_div(lev_c, lev_p)
        tata = _safe_div((ni_c - ocf_c) if ni_c is not None and ocf_c is not None else None, ta_c)

        if any(v is None for v in (dsri, gmi, aqi, sgi, depi, sgai, lvgi, tata)):
            return None

        m_score = (
            -4.84
            + (0.920 * (dsri or 0.0))
            + (0.528 * (gmi or 0.0))
            + (0.404 * (aqi or 0.0))
            + (0.892 * (sgi or 0.0))
            + (0.115 * (depi or 0.0))
            - (0.172 * (sgai or 0.0))
            + (4.679 * (tata or 0.0))
            - (0.327 * (lvgi or 0.0))
        )
        components = {
            "dsri": dsri,
            "gmi": gmi,
            "aqi": aqi,
            "sgi": sgi,
            "depi": depi,
            "sgai": sgai,
            "lvgi": lvgi,
            "tata": tata,
        }
        return {
            "m_score": float(m_score),
            "components": {k: float(v or 0.0) for k, v in components.items()},
            "likely_manipulator": bool(m_score > -1.78),
        }
    except Exception as exc:
        LOG.debug("Beneish M-score failed for %s: %s", ticker, exc)
        return None


def compute_altman_z_score(ticker: str) -> dict[str, Any] | None:
    """
    Altman Z-Score:
      Z = 1.2*A + 1.4*B + 3.3*C + 0.6*D + 1.0*E
    """
    try:
        import yfinance as yf

        tkr = _normalize_symbol(ticker)
        t = yf.Ticker(tkr)
        bs = t.quarterly_balance_sheet
        fin = t.quarterly_financials
        info = t.info or {}
        if bs is None or bs.empty or fin is None or fin.empty:
            return None

        total_assets = _safe_get_col_value(bs, 0, "Total Assets")
        if total_assets is None:
            return None

        current_assets = _safe_get_col_value(bs, 0, "Current Assets")
        current_liabilities = _safe_get_col_value(bs, 0, "Current Liabilities")
        if current_liabilities is None:
            current_liabilities = _safe_get_col_value(bs, 0, "Total Current Liabilities")
        retained_earnings = _safe_get_col_value(bs, 0, "Retained Earnings")
        total_liabilities = _safe_get_col_value(bs, 0, "Total Liabilities Net Minority Interest")
        if total_liabilities is None:
            total_liabilities = _safe_get_col_value(bs, 0, "Total Liab")

        ebit = _safe_get_col_value(fin, 0, "EBIT")
        if ebit is None:
            ebit = _safe_get_col_value(fin, 0, "Operating Income")
        revenue = _safe_get_col_value(fin, 0, "Total Revenue")
        market_cap = float(info.get("marketCap", 0) or 0)

        working_capital = None
        if current_assets is not None and current_liabilities is not None:
            working_capital = current_assets - current_liabilities

        a_val = _safe_div(working_capital, total_assets)
        b_val = _safe_div(retained_earnings, total_assets)
        c_val = _safe_div(ebit, total_assets)
        d_val = _safe_div(market_cap, total_liabilities)
        e_val = _safe_div(revenue, total_assets)
        if any(v is None for v in (a_val, b_val, c_val, d_val, e_val)):
            return None

        z_score = (1.2 * (a_val or 0.0)) + (1.4 * (b_val or 0.0)) + (3.3 * (c_val or 0.0)) + (0.6 * (d_val or 0.0)) + (1.0 * (e_val or 0.0))
        zone = "safe" if z_score > 3.0 else "grey" if z_score >= 1.8 else "distress"
        return {
            "z_score": float(z_score),
            "zone": zone,
            "components": {
                "A_working_capital_over_assets": float(a_val or 0.0),
                "B_retained_earnings_over_assets": float(b_val or 0.0),
                "C_ebit_over_assets": float(c_val or 0.0),
                "D_market_cap_over_liabilities": float(d_val or 0.0),
                "E_revenue_over_assets": float(e_val or 0.0),
            },
        }
    except Exception as exc:
        LOG.debug("Altman Z-score failed for %s: %s", ticker, exc)
        return None


def _cache_path(skill_dir: Path | None = None) -> Path:
    return (skill_dir or SKILL_DIR) / FORENSIC_CACHE_FILE


def _load_cache(skill_dir: Path | None = None) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    if not path.exists():
        return {"tickers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tickers"), dict):
            return data
    except Exception:
        pass
    return {"tickers": {}}


def _save_cache(cache: dict[str, Any], skill_dir: Path | None = None) -> None:
    path = _cache_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _is_fresh(entry: dict[str, Any] | None, cache_hours: float) -> bool:
    if not entry:
        return False
    ts = float(entry.get("timestamp", 0) or 0)
    if ts <= 0:
        return False
    age_h = (time.time() - ts) / 3600.0
    return age_h <= float(cache_hours)


def _build_forensic_flags(
    sloan: dict[str, Any] | None,
    beneish: dict[str, Any] | None,
    altman: dict[str, Any] | None,
    sloan_max: float,
    beneish_max: float,
    altman_min: float,
) -> list[str]:
    flags: list[str] = []
    if sloan is not None:
        ratio = sloan.get("sloan_ratio")
        if isinstance(ratio, (int, float)) and float(ratio) > float(sloan_max):
            flags.append("sloan_high")
    if beneish is not None:
        m_score = beneish.get("m_score")
        if isinstance(m_score, (int, float)) and float(m_score) > float(beneish_max):
            flags.append("beneish_manipulator")
    if altman is not None:
        z_score = altman.get("z_score")
        if isinstance(z_score, (int, float)) and float(z_score) < float(altman_min):
            flags.append("altman_distress")
    return flags


def compute_forensic_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    cache_hours: float = 24.0,
    sloan_max: float = 0.10,
    beneish_max: float = -1.78,
    altman_min: float = 1.80,
) -> dict[str, Any]:
    tkr = _normalize_symbol(ticker)
    cache = _load_cache(skill_dir)
    entry = (cache.get("tickers") or {}).get(tkr)
    if _is_fresh(entry, cache_hours):
        payload: dict[str, Any] = dict((entry or {}).get("payload") or {})
        payload["from_cache"] = True
        sloan = payload.get("sloan")
        beneish = payload.get("beneish")
        altman = payload.get("altman")
        payload["forensic_flags"] = _build_forensic_flags(
            sloan, beneish, altman, sloan_max, beneish_max, altman_min
        )
        return payload

    sloan = compute_sloan_ratio(tkr)
    beneish = compute_beneish_m_score(tkr)
    altman = compute_altman_z_score(tkr)
    payload = {
        "ok": any(x is not None for x in (sloan, beneish, altman)),
        "ticker": tkr,
        "sloan": sloan,
        "beneish": beneish,
        "altman": altman,
        "forensic_flags": _build_forensic_flags(
            sloan, beneish, altman, sloan_max, beneish_max, altman_min
        ),
        "from_cache": False,
    }

    cache.setdefault("tickers", {})[tkr] = {"timestamp": time.time(), "payload": payload}
    try:
        _save_cache(cache, skill_dir)
    except Exception as exc:
        LOG.debug("Forensic cache write failed: %s", exc)
    return payload
