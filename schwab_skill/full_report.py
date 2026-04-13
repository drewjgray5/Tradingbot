"""
Full Financial Report generator -- standalone module.

Usage:
    python full_report.py TICKER              # prints markdown report to stdout
    python full_report.py TICKER --discord    # sends report sections to Discord
    python full_report.py TICKER --json       # outputs structured JSON

Programmatic:
    from full_report import generate_full_report
    sections = generate_full_report("AAPL")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from config import _load_env

LOG = logging.getLogger(__name__)

DISCORD_SECTION_LIMIT = 1900
DISCORD_TITLE_MAX = 256
DISCORD_DESC_MAX = 4096
DISCORD_FIELD_NAME_MAX = 256
DISCORD_FIELD_VALUE_MAX = 1024
DISCORD_FIELDS_MAX = 25
DISCORD_FOOTER_MAX = 2048


# ---------------------------------------------------------------------------
# Data classes for structured output
# ---------------------------------------------------------------------------

@dataclass
class TechnicalSection:
    ticker: str = ""
    current_price: float = 0.0
    sma_50: float = 0.0
    sma_150: float = 0.0
    sma_200: float = 0.0
    atr_14: float = 0.0
    high_52w: float = 0.0
    low_52w: float = 0.0
    pct_from_high: float = 0.0
    stage_2: bool = False
    vcp: bool = False
    signal_score: float = 0.0
    sector_etf: str = ""
    sector_winning: bool = False
    avg_vol_50: float = 0.0
    last_volume: float = 0.0


@dataclass
class DCFSection:
    revenue_history: list[dict] = field(default_factory=list)
    fcf_history: list[dict] = field(default_factory=list)
    growth_rate: float = 0.0
    wacc: float = 0.10
    terminal_growth: float = 0.025
    projected_fcf: list[dict] = field(default_factory=list)
    terminal_value: float = 0.0
    enterprise_value: float = 0.0
    net_debt: float = 0.0
    shares_outstanding: float = 0.0
    intrinsic_value: float = 0.0
    current_price: float = 0.0
    margin_of_safety: float = 0.0
    sensitivity: list[dict] = field(default_factory=list)
    error: str = ""


@dataclass
class CompsSection:
    ticker: str = ""
    peers: list[dict] = field(default_factory=list)
    median_pe: float = 0.0
    median_ps: float = 0.0
    median_ev_ebitda: float = 0.0
    implied_price_pe: float = 0.0
    implied_price_ps: float = 0.0
    error: str = ""


@dataclass
class HealthSection:
    current_ratio: float = 0.0
    debt_to_equity: float = 0.0
    interest_coverage: float = 0.0
    roe: float = 0.0
    operating_margin: float = 0.0
    flags: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class EdgarSection:
    cik: str = ""
    recent_filings: list[dict] = field(default_factory=list)
    risk_tag: str = "unknown"
    risk_reasons: list[str] = field(default_factory=list)
    recent_8k: bool = False
    filing_recency_days: int | None = None
    from_cache: bool = False
    filing_analysis: dict[str, Any] | None = None
    analysis_error: str = ""
    error: str = ""


@dataclass
class MiroFishSection:
    conviction_score: int = 0
    summary: str = ""
    agent_votes: list[dict] = field(default_factory=list)
    continuation_probability: float = 0.0
    bull_trap_probability: float = 0.0
    error: str = ""


@dataclass
class FullReport:
    ticker: str = ""
    generated_at: str = ""
    technical: TechnicalSection | None = None
    dcf: DCFSection | None = None
    comps: CompsSection | None = None
    health: HealthSection | None = None
    edgar: EdgarSection | None = None
    mirofish: MiroFishSection | None = None
    synthesis: str = ""


# ---------------------------------------------------------------------------
# 1. Technical Analysis
# ---------------------------------------------------------------------------

def _resolve_skill_dir(skill_dir: Path | None) -> Path:
    return SKILL_DIR if skill_dir is None else Path(skill_dir)


def _build_technical(ticker: str, df: pd.DataFrame, auth: Any, skill_dir: Path | None = None) -> TechnicalSection:
    from sector_strength import get_ticker_sector_etf
    from stage_analysis import add_indicators, check_vcp_volume, compute_signal_score, is_stage_2

    sd = _resolve_skill_dir(skill_dir)
    sec = TechnicalSection(ticker=ticker)

    if df.empty or len(df) < 5:
        return sec

    df = add_indicators(df)
    latest = df.iloc[-1]
    sec.current_price = float(latest["close"])
    sec.sma_50 = float(latest.get("sma_50", 0) or 0)
    sec.sma_150 = float(latest.get("sma_150", 0) or 0)
    sec.sma_200 = float(latest.get("sma_200", 0) or 0)
    sec.atr_14 = float(latest.get("atr_14", 0) or 0)
    sec.avg_vol_50 = float(latest.get("avg_vol_50", 0) or 0)
    sec.last_volume = float(latest.get("volume", 0) or 0)

    lookback = min(252, len(df))
    sec.high_52w = float(df["high"].iloc[-lookback:].max())
    sec.low_52w = float(df["low"].iloc[-lookback:].min())
    sec.pct_from_high = (sec.current_price / sec.high_52w * 100) if sec.high_52w > 0 else 0

    sec.stage_2 = is_stage_2(df, sd)
    sec.vcp = check_vcp_volume(df, sd)
    sec.signal_score = compute_signal_score(df)

    etf = get_ticker_sector_etf(ticker)
    sec.sector_etf = etf or "Unknown"

    return sec


# ---------------------------------------------------------------------------
# 2. DCF Model
# ---------------------------------------------------------------------------

def _build_dcf(ticker: str) -> DCFSection:
    sec = DCFSection()
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        cashflow = t.cashflow
        financials = t.financials
        balance = t.balance_sheet

        if cashflow is None or cashflow.empty:
            sec.error = "No cash flow data available from yfinance."
            return sec

        fcf_row = None
        for label in ["Free Cash Flow", "FreeCashFlow"]:
            if label in cashflow.index:
                fcf_row = cashflow.loc[label]
                break
        if fcf_row is None:
            op_cf = None
            capex = None
            for label in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
                if label in cashflow.index:
                    op_cf = cashflow.loc[label]
                    break
            for label in ["Capital Expenditure", "Capital Expenditures"]:
                if label in cashflow.index:
                    capex = cashflow.loc[label]
                    break
            if op_cf is not None and capex is not None:
                fcf_row = op_cf + capex  # capex is typically negative
            else:
                sec.error = "Cannot derive FCF from cash flow statement."
                return sec

        fcf_values = fcf_row.dropna().sort_index()
        if len(fcf_values) < 2:
            sec.error = "Insufficient FCF history for projection."
            return sec

        for dt, val in fcf_values.items():
            sec.fcf_history.append({"year": str(dt.year) if hasattr(dt, "year") else str(dt), "fcf": float(val)})

        if financials is not None and not financials.empty:
            for label in ["Total Revenue", "Revenue"]:
                if label in financials.index:
                    rev = financials.loc[label].dropna().sort_index()
                    for dt, val in rev.items():
                        sec.revenue_history.append({"year": str(dt.year) if hasattr(dt, "year") else str(dt), "revenue": float(val)})
                    break

        fcf_list = [float(v) for v in fcf_values.values if float(v) > 0]
        if len(fcf_list) >= 2:
            cagr = (fcf_list[-1] / fcf_list[0]) ** (1 / (len(fcf_list) - 1)) - 1
            sec.growth_rate = max(-0.10, min(0.30, cagr))
        else:
            sec.growth_rate = 0.05

        last_fcf = float(fcf_values.iloc[-1])
        if last_fcf <= 0:
            last_fcf = abs(last_fcf) if abs(last_fcf) > 0 else 1e6
            sec.growth_rate = max(sec.growth_rate, 0.05)

        wacc = sec.wacc
        tg = sec.terminal_growth

        projected = []
        pv_sum = 0.0
        cf = last_fcf
        for yr in range(1, 6):
            cf *= (1 + sec.growth_rate)
            pv = cf / ((1 + wacc) ** yr)
            pv_sum += pv
            projected.append({"year": yr, "fcf": round(cf, 2), "pv": round(pv, 2)})
        sec.projected_fcf = projected

        terminal_cf = cf * (1 + tg)
        sec.terminal_value = terminal_cf / (wacc - tg)
        pv_terminal = sec.terminal_value / ((1 + wacc) ** 5)
        sec.enterprise_value = pv_sum + pv_terminal

        total_debt = 0.0
        cash = 0.0
        if balance is not None and not balance.empty:
            for label in ["Total Debt", "Long Term Debt", "Long Term Debt And Capital Lease Obligation"]:
                if label in balance.index:
                    v = balance.loc[label].dropna()
                    if len(v):
                        total_debt = float(v.iloc[0])
                    break
            for label in ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]:
                if label in balance.index:
                    v = balance.loc[label].dropna()
                    if len(v):
                        cash = float(v.iloc[0])
                    break
        sec.net_debt = total_debt - cash

        sec.shares_outstanding = float(info.get("sharesOutstanding", 0) or 0)
        if sec.shares_outstanding <= 0:
            for label in ["Share Issued", "Ordinary Shares Number"]:
                if balance is not None and label in balance.index:
                    v = balance.loc[label].dropna()
                    if len(v):
                        sec.shares_outstanding = float(v.iloc[0])
                    break

        equity_value = sec.enterprise_value - sec.net_debt
        if sec.shares_outstanding > 0:
            sec.intrinsic_value = equity_value / sec.shares_outstanding
        else:
            sec.error = "Shares outstanding unavailable."
            return sec

        sec.current_price = float(info.get("currentPrice", 0) or info.get("previousClose", 0) or 0)
        if sec.current_price > 0:
            sec.margin_of_safety = (sec.intrinsic_value - sec.current_price) / sec.current_price * 100

        sensitivity = []
        for w in [0.08, 0.09, 0.10, 0.11, 0.12]:
            row: dict[str, Any] = {"wacc": w}
            for g in [0.02, 0.025, 0.03]:
                if w <= g:
                    row[f"tg_{g}"] = None
                    continue
                pv_s = 0.0
                c = last_fcf
                for yr in range(1, 6):
                    c *= (1 + sec.growth_rate)
                    pv_s += c / ((1 + w) ** yr)
                tc = c * (1 + g) / (w - g)
                pv_t = tc / ((1 + w) ** 5)
                ev = pv_s + pv_t - sec.net_debt
                iv = ev / sec.shares_outstanding if sec.shares_outstanding > 0 else 0
                row[f"tg_{g}"] = round(iv, 2)
            sensitivity.append(row)
        sec.sensitivity = sensitivity

    except Exception as e:
        sec.error = str(e)
    return sec


# ---------------------------------------------------------------------------
# 3. Comparable Analysis
# ---------------------------------------------------------------------------

def _build_comps(ticker: str) -> CompsSection:
    sec = CompsSection(ticker=ticker)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        sector = info.get("sector", "")
        industry = info.get("industry", "")

        target_eps = info.get("trailingEps", 0) or 0
        target_rev_per_share = info.get("revenuePerShare", 0) or 0

        peers = []
        peer_tickers = _find_peers(ticker, sector, industry)

        for pt in peer_tickers[:6]:
            try:
                pi = yf.Ticker(pt).info or {}
                peers.append({
                    "ticker": pt,
                    "name": pi.get("shortName", pt),
                    "pe": pi.get("trailingPE"),
                    "ps": pi.get("priceToSalesTrailing12Months"),
                    "ev_ebitda": pi.get("enterpriseToEbitda"),
                    "market_cap": pi.get("marketCap"),
                })
                time.sleep(0.2)
            except Exception:
                continue

        sec.peers = peers
        pes = [p["pe"] for p in peers if p.get("pe") and p["pe"] > 0]
        pss = [p["ps"] for p in peers if p.get("ps") and p["ps"] > 0]
        evs = [p["ev_ebitda"] for p in peers if p.get("ev_ebitda") and p["ev_ebitda"] > 0]

        if pes:
            sec.median_pe = sorted(pes)[len(pes) // 2]
            if target_eps and target_eps > 0:
                sec.implied_price_pe = sec.median_pe * target_eps
        if pss:
            sec.median_ps = sorted(pss)[len(pss) // 2]
            if target_rev_per_share and target_rev_per_share > 0:
                sec.implied_price_ps = sec.median_ps * target_rev_per_share
        if evs:
            sec.median_ev_ebitda = sorted(evs)[len(evs) // 2]

    except Exception as e:
        sec.error = str(e)
    return sec


def _find_peers(ticker: str, sector: str, industry: str) -> list[str]:
    """Find comparable tickers using yfinance sector/industry data."""

    well_known: dict[str, list[str]] = {
        "Technology": ["AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMD", "CRM", "ADBE", "ORCL", "INTC"],
        "Financial Services": ["JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW"],
        "Healthcare": ["JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT"],
        "Consumer Cyclical": ["AMZN", "TSLA", "HD", "NKE", "MCD", "SBUX", "TGT", "LOW"],
        "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO"],
        "Industrials": ["CAT", "HON", "UNP", "BA", "GE", "RTX", "LMT", "DE"],
        "Communication Services": ["GOOGL", "META", "DIS", "NFLX", "CMCSA", "T", "VZ", "TMUS"],
        "Consumer Defensive": ["PG", "KO", "PEP", "WMT", "COST", "CL", "MDLZ"],
        "Basic Materials": ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "DD"],
        "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE"],
        "Real Estate": ["AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA"],
    }

    candidates = well_known.get(sector, [])
    if not candidates:
        for k, v in well_known.items():
            if k.lower() in (sector or "").lower():
                candidates = v
                break

    ticker_upper = ticker.upper()
    return [c for c in candidates if c != ticker_upper][:6]


# ---------------------------------------------------------------------------
# 4. Financial Health
# ---------------------------------------------------------------------------

def _build_health(ticker: str) -> HealthSection:
    sec = HealthSection()
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        financials = t.financials

        sec.current_ratio = float(info.get("currentRatio", 0) or 0)
        sec.debt_to_equity = float(info.get("debtToEquity", 0) or 0) / 100.0 if info.get("debtToEquity") else 0.0
        sec.roe = float(info.get("returnOnEquity", 0) or 0)
        sec.operating_margin = float(info.get("operatingMargins", 0) or 0)

        if financials is not None and not financials.empty:
            ebit = None
            for label in ["EBIT", "Operating Income"]:
                if label in financials.index:
                    v = financials.loc[label].dropna()
                    if len(v):
                        ebit = float(v.iloc[0])
                    break
            interest_exp = None
            for label in ["Interest Expense", "Interest Expense Non Operating"]:
                if label in financials.index:
                    v = financials.loc[label].dropna()
                    if len(v):
                        interest_exp = abs(float(v.iloc[0]))
                    break
            if ebit and interest_exp and interest_exp > 0:
                sec.interest_coverage = ebit / interest_exp

        if sec.current_ratio > 0 and sec.current_ratio < 1.0:
            sec.flags.append("LOW CURRENT RATIO (<1.0) -- potential liquidity risk")
        if sec.debt_to_equity > 2.0:
            sec.flags.append(f"HIGH LEVERAGE -- D/E ratio {sec.debt_to_equity:.2f}")
        if sec.interest_coverage > 0 and sec.interest_coverage < 2.0:
            sec.flags.append(f"LOW INTEREST COVERAGE ({sec.interest_coverage:.1f}x)")
        if sec.roe < 0:
            sec.flags.append("NEGATIVE ROE -- company is losing money on equity")
        if sec.operating_margin < 0:
            sec.flags.append("NEGATIVE OPERATING MARGIN")

    except Exception as e:
        sec.error = str(e)
    return sec


# ---------------------------------------------------------------------------
# 5. SEC EDGAR
# ---------------------------------------------------------------------------

def _build_edgar(ticker: str, skill_dir: Path | None = None) -> EdgarSection:
    sd = _resolve_skill_dir(skill_dir)
    sec = EdgarSection()
    try:
        from config import (
            get_edgar_user_agent,
            get_sec_cache_hours,
            get_sec_enrichment_enabled,
            get_sec_filing_analysis_enabled,
            get_sec_filing_cache_hours,
            get_sec_filing_llm_summary_enabled,
            get_sec_filing_max_chars,
        )
        enabled = get_sec_enrichment_enabled(sd)
        cache_hours = get_sec_cache_hours(sd)
        user_agent = get_edgar_user_agent(sd)
        filing_analysis_enabled = get_sec_filing_analysis_enabled(sd)
        filing_llm_enabled = get_sec_filing_llm_summary_enabled(sd)
        filing_cache_hours = get_sec_filing_cache_hours(sd)
        filing_max_chars = get_sec_filing_max_chars(sd)
    except Exception:
        enabled = True
        cache_hours = 12.0
        filing_analysis_enabled = True
        filing_llm_enabled = True
        filing_cache_hours = 24.0
        filing_max_chars = 120000
        env = _load_env()
        user_agent = env.get("EDGAR_USER_AGENT", os.environ.get("EDGAR_USER_AGENT", "SchwabTradingBot contact@example.com"))

    try:
        from sec_enrichment import fetch_sec_snapshot
        snap = fetch_sec_snapshot(
            ticker,
            skill_dir=sd,
            user_agent=user_agent,
            cache_hours=cache_hours,
            enabled=enabled,
        )
        sec.cik = snap.get("cik", "") or ""
        sec.recent_filings = snap.get("recent_filings", []) or []
        sec.risk_tag = snap.get("risk_tag", "unknown") or "unknown"
        sec.risk_reasons = snap.get("risk_reasons", []) or []
        sec.recent_8k = bool(snap.get("recent_8k", False))
        sec.filing_recency_days = snap.get("filing_recency_days")
        sec.from_cache = bool(snap.get("from_cache", False))
        if not snap.get("ok"):
            sec.error = snap.get("error", "SEC enrichment failed")
        elif filing_analysis_enabled:
            try:
                from sec_filing_compare import analyze_latest_filing_for_ticker

                filing_analysis = analyze_latest_filing_for_ticker(
                    ticker=ticker,
                    form_type="10-K",
                    user_agent=user_agent,
                    skill_dir=sd,
                    cache_hours=filing_cache_hours,
                    max_chars=filing_max_chars,
                    enable_llm=filing_llm_enabled,
                )
                if filing_analysis.get("ok"):
                    sec.filing_analysis = filing_analysis
                else:
                    sec.analysis_error = str(filing_analysis.get("error", "Filing analysis unavailable"))
            except Exception as analysis_exc:
                sec.analysis_error = str(analysis_exc)
    except Exception as e:
        sec.error = str(e)
    return sec


# ---------------------------------------------------------------------------
# 6. MiroFish Simulation
# ---------------------------------------------------------------------------

def _build_mirofish(ticker: str, df: pd.DataFrame, auth: Any, skill_dir: Path | None = None) -> MiroFishSection:
    sd = _resolve_skill_dir(skill_dir)
    sec = MiroFishSection()
    try:
        from engine_analysis import MarketSimulation
        sim = MarketSimulation(ticker=ticker, seed_df=df, auth=auth, skill_dir=sd)
        result = sim.run()
        sec.conviction_score = result.get("conviction_score", 0)
        sec.summary = result.get("summary", "")
        sec.agent_votes = result.get("agent_votes", [])
        sec.continuation_probability = result.get("continuation_probability", 0)
        sec.bull_trap_probability = result.get("bull_trap_probability", 0)
    except Exception as e:
        sec.error = str(e)
    return sec


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def _synthesize(report: FullReport) -> str:
    lines = [f"## Synthesis -- {report.ticker}\n"]

    t = report.technical
    if t:
        if t.stage_2 and t.vcp:
            lines.append("- **Technical**: Stage 2 confirmed + VCP volume contraction. Setup is actionable.")
        elif t.stage_2:
            lines.append("- **Technical**: Stage 2 confirmed but VCP not met. Watch for volume dry-up.")
        else:
            lines.append("- **Technical**: Does NOT meet Stage 2 criteria. Not a breakout candidate.")
        lines.append(f"- **Signal Score**: {t.signal_score:.1f}/100")

    d = report.dcf
    if d and not d.error:
        if d.margin_of_safety > 20:
            lines.append(f"- **DCF**: Intrinsic value ${d.intrinsic_value:.2f} -- {d.margin_of_safety:+.1f}% margin of safety. **Undervalued.**")
        elif d.margin_of_safety > 0:
            lines.append(f"- **DCF**: Intrinsic value ${d.intrinsic_value:.2f} -- {d.margin_of_safety:+.1f}% margin. Fairly valued.")
        else:
            lines.append(f"- **DCF**: Intrinsic value ${d.intrinsic_value:.2f} -- {d.margin_of_safety:+.1f}% margin. **Overvalued.**")
    elif d and d.error:
        lines.append(f"- **DCF**: Unavailable ({d.error})")

    c = report.comps
    if c and not c.error:
        parts = []
        if c.implied_price_pe > 0:
            parts.append(f"P/E -> ${c.implied_price_pe:.2f}")
        if c.implied_price_ps > 0:
            parts.append(f"P/S -> ${c.implied_price_ps:.2f}")
        if parts:
            lines.append(f"- **Comps**: Implied prices: {', '.join(parts)}")

    h = report.health
    if h and not h.error:
        if h.flags:
            lines.append(f"- **Health**: {len(h.flags)} flag(s): {'; '.join(h.flags)}")
        else:
            lines.append("- **Health**: No red flags. Financials appear solid.")

    m = report.mirofish
    if m and not m.error:
        lines.append(f"- **MiroFish**: Conviction {m.conviction_score:+d}/100 -- {m.summary}")

    e = report.edgar
    if e and not e.error and e.recent_filings:
        latest = e.recent_filings[0]
        lines.append(f"- **EDGAR**: Latest filing: {latest['form']} ({latest['date']}); risk tag: {e.risk_tag.upper()}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown formatters (Discord-ready)
# ---------------------------------------------------------------------------

def _fmt_technical(t: TechnicalSection) -> str:
    stage = "YES" if t.stage_2 else "NO"
    vcp = "YES" if t.vcp else "NO"
    return (
        f"## Technical Analysis -- {t.ticker}\n"
        f"```\n"
        f"Price        ${t.current_price:>12,.2f}\n"
        f"52w High     ${t.high_52w:>12,.2f}  ({t.pct_from_high:.1f}% of high)\n"
        f"52w Low      ${t.low_52w:>12,.2f}\n"
        f"SMA 50       ${t.sma_50:>12,.2f}\n"
        f"SMA 150      ${t.sma_150:>12,.2f}\n"
        f"SMA 200      ${t.sma_200:>12,.2f}\n"
        f"ATR-14       ${t.atr_14:>12,.2f}\n"
        f"Volume (last) {t.last_volume:>12,.0f}\n"
        f"Avg Vol 50d   {t.avg_vol_50:>12,.0f}\n"
        f"```\n"
        f"| Check | Result |\n"
        f"|-------|--------|\n"
        f"| Stage 2 | {stage} |\n"
        f"| VCP Volume | {vcp} |\n"
        f"| Signal Score | {t.signal_score:.1f}/100 |\n"
        f"| Sector ETF | {t.sector_etf} |\n"
    )


def _fmt_dcf(d: DCFSection) -> str:
    if d.error:
        return f"## DCF Model\n[!] {d.error}\n"

    lines = [
        "## DCF Model\n",
        f"**Assumptions**: Growth {d.growth_rate*100:.1f}% | WACC {d.wacc*100:.1f}% | Terminal Growth {d.terminal_growth*100:.1f}%\n",
    ]

    if d.fcf_history:
        lines.append("**Historical FCF**:")
        lines.append("```")
        for h in d.fcf_history[-4:]:
            lines.append(f"  {h['year']}:  ${h['fcf']/1e9:>8.2f}B")
        lines.append("```")

    if d.projected_fcf:
        lines.append("**Projected FCF (5yr)**:")
        lines.append("```")
        for p in d.projected_fcf:
            lines.append(f"  Year {p['year']}:  ${p['fcf']/1e9:>8.2f}B  (PV: ${p['pv']/1e9:.2f}B)")
        lines.append("```")

    lines.append(f"**Enterprise Value**: ${d.enterprise_value/1e9:.2f}B")
    lines.append(f"**Net Debt**: ${d.net_debt/1e9:.2f}B")
    lines.append(f"**Shares Outstanding**: {d.shares_outstanding/1e9:.2f}B")
    lines.append(f"**Intrinsic Value**: ${d.intrinsic_value:.2f}")
    lines.append(f"**Current Price**: ${d.current_price:.2f}")
    lines.append(f"**Margin of Safety**: {d.margin_of_safety:+.1f}%\n")

    if d.sensitivity:
        lines.append("**Sensitivity (WACC vs Terminal Growth)**:")
        lines.append("```")
        lines.append(f"{'WACC':>6}  {'TG 2.0%':>10}  {'TG 2.5%':>10}  {'TG 3.0%':>10}")
        lines.append("-" * 42)
        for row in d.sensitivity:
            v1 = f"${row.get('tg_0.02', 0) or 0:.2f}" if row.get('tg_0.02') else "  N/A"
            v2 = f"${row.get('tg_0.025', 0) or 0:.2f}" if row.get('tg_0.025') else "  N/A"
            v3 = f"${row.get('tg_0.03', 0) or 0:.2f}" if row.get('tg_0.03') else "  N/A"
            lines.append(f"{row['wacc']*100:>5.1f}%  {v1:>10}  {v2:>10}  {v3:>10}")
        lines.append("```")

    return "\n".join(lines)


def _fmt_comps(c: CompsSection) -> str:
    if c.error:
        return f"## Comparable Analysis -- {c.ticker}\n[!] {c.error}\n"

    lines = [f"## Comparable Analysis -- {c.ticker}\n"]
    if c.peers:
        lines.append("```")
        lines.append(f"{'Ticker':<8} {'P/E':>8} {'P/S':>8} {'EV/EBITDA':>10}  {'Mkt Cap':>12}")
        lines.append("-" * 52)
        for p in c.peers:
            pe = f"{p['pe']:.1f}" if p.get("pe") else "N/A"
            ps = f"{p['ps']:.1f}" if p.get("ps") else "N/A"
            ev = f"{p['ev_ebitda']:.1f}" if p.get("ev_ebitda") else "N/A"
            mc = f"${p['market_cap']/1e9:.1f}B" if p.get("market_cap") else "N/A"
            lines.append(f"{p['ticker']:<8} {pe:>8} {ps:>8} {ev:>10}  {mc:>12}")
        lines.append("```")

    lines.append(f"**Median P/E**: {c.median_pe:.1f} | **Median P/S**: {c.median_ps:.1f} | **Median EV/EBITDA**: {c.median_ev_ebitda:.1f}")
    impl = []
    if c.implied_price_pe > 0:
        impl.append(f"P/E -> ${c.implied_price_pe:.2f}")
    if c.implied_price_ps > 0:
        impl.append(f"P/S -> ${c.implied_price_ps:.2f}")
    if impl:
        lines.append(f"**Implied Price**: {' | '.join(impl)}")

    return "\n".join(lines)


def _fmt_health(h: HealthSection) -> str:
    if h.error:
        return f"## Financial Health\n[!] {h.error}\n"

    lines = [
        "## Financial Health\n",
        "```",
        f"Current Ratio       {h.current_ratio:>8.2f}",
        f"Debt/Equity         {h.debt_to_equity:>8.2f}",
        f"Interest Coverage   {h.interest_coverage:>8.1f}x",
        f"ROE                 {h.roe*100:>8.1f}%",
        f"Operating Margin    {h.operating_margin*100:>8.1f}%",
        "```",
    ]
    if h.flags:
        lines.append("\n**Flags**:")
        for f in h.flags:
            lines.append(f"- [!] {f}")
    else:
        lines.append("\nNo red flags detected.")

    return "\n".join(lines)


def _fmt_edgar(e: EdgarSection) -> str:
    if e.error:
        return f"## SEC EDGAR\n[!] {e.error}\n"

    lines = [f"## SEC EDGAR (CIK: {e.cik})\n"]
    lines.append(
        f"**Risk Tag**: {e.risk_tag.upper()} | "
        f"**Recent 8-K**: {'YES' if e.recent_8k else 'NO'} | "
        f"**Recency (days)**: {e.filing_recency_days if e.filing_recency_days is not None else 'N/A'}"
    )
    if e.risk_reasons:
        lines.append("**Risk Notes**:")
        for r in e.risk_reasons:
            lines.append(f"- {r}")
    if e.from_cache:
        lines.append("_SEC data source: cache_")
    if e.recent_filings:
        lines.append("| Form | Date | Description |")
        lines.append("|------|------|-------------|")
        for f in e.recent_filings:
            desc = f.get("description", "")[:60]
            lines.append(f"| {f['form']} | {f['date']} | [{desc}]({f['url']}) |")
    if e.filing_analysis and isinstance(e.filing_analysis, dict):
        lines.append("\n**Filing Analysis Takeaway**:")
        lines.append(f"- {e.filing_analysis.get('high_level_takeaway', 'No takeaway generated.')}")
        llm_summary = str(e.filing_analysis.get("llm_summary", "") or "").strip()
        if llm_summary:
            lines.append("- LLM Summary:")
            lines.append(f"  {llm_summary}")
    elif e.analysis_error:
        lines.append(f"\n_Filing analysis note: {e.analysis_error}_")
    return "\n".join(lines)


def _fmt_mirofish(m: MiroFishSection) -> str:
    if m.error:
        return f"## MiroFish Simulation\n[!] {m.error}\n"

    lines = [
        "## MiroFish Simulation\n",
        f"**Crowd Conviction**: {m.conviction_score:+d}/100 -- {m.summary}",
        f"**Continuation Prob**: {m.continuation_probability:.0%} | **Bull Trap Prob**: {m.bull_trap_probability:.0%}\n",
    ]
    if m.agent_votes:
        lines.append("```")
        lines.append(f"{'Agent':<22} {'Score':>6}  Reason")
        lines.append("-" * 60)
        for v in m.agent_votes:
            name = v.get("name", "?")[:20]
            score = v.get("score", 0)
            reason = (v.get("reason", "") or "")[:40]
            lines.append(f"{name:<22} {score:>+5d}  {reason}")
        lines.append("```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def generate_full_report(
    ticker: str,
    skip_mirofish: bool = False,
    skip_edgar: bool = False,
    auth: Any = None,
    skill_dir: Path | None = None,
) -> FullReport:
    """
    Generate a complete financial report for a ticker.
    Returns a FullReport dataclass with all sections populated.
    """
    from datetime import datetime, timezone

    sd = _resolve_skill_dir(skill_dir)
    ticker = ticker.upper().strip()
    report = FullReport(ticker=ticker, generated_at=datetime.now(timezone.utc).isoformat())

    # Fetch price data (yfinance fallback built into market_data)
    try:
        from market_data import get_daily_history
        df = get_daily_history(ticker, days=400, auth=auth, skill_dir=sd)
    except Exception:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            raw = t.history(period="2y", auto_adjust=True)
            df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
            df = df[["open", "high", "low", "close", "volume"]].sort_index()
        except Exception:
            df = pd.DataFrame()

    LOG.info("Building technical analysis for %s ...", ticker)
    report.technical = _build_technical(ticker, df, auth, sd)

    LOG.info("Building DCF model for %s ...", ticker)
    report.dcf = _build_dcf(ticker)

    LOG.info("Building comparable analysis for %s ...", ticker)
    report.comps = _build_comps(ticker)

    LOG.info("Building financial health snapshot for %s ...", ticker)
    report.health = _build_health(ticker)

    if not skip_edgar:
        LOG.info("Fetching SEC EDGAR filings for %s ...", ticker)
        report.edgar = _build_edgar(ticker, sd)

    if not skip_mirofish:
        LOG.info("Running MiroFish simulation for %s ...", ticker)
        report.mirofish = _build_mirofish(ticker, df, auth, sd)

    report.synthesis = _synthesize(report)
    return report


def report_to_markdown(report: FullReport) -> str:
    """Convert a FullReport to a single markdown string."""
    sections = [f"# Full Financial Report -- {report.ticker}\n_Generated: {report.generated_at}_\n"]

    if report.technical:
        sections.append(_fmt_technical(report.technical))
    if report.dcf:
        sections.append(_fmt_dcf(report.dcf))
    if report.comps:
        sections.append(_fmt_comps(report.comps))
    if report.health:
        sections.append(_fmt_health(report.health))
    if report.edgar:
        sections.append(_fmt_edgar(report.edgar))
    if report.mirofish:
        sections.append(_fmt_mirofish(report.mirofish))
    if report.synthesis:
        sections.append(report.synthesis)

    return "\n---\n".join(sections)


def _clip_text(text: Any, limit: int, suffix: str = "...") -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    if limit <= len(suffix):
        return raw[:limit]
    return raw[: limit - len(suffix)] + suffix


def _sanitize_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for f in fields[:DISCORD_FIELDS_MAX]:
        name = _clip_text(f.get("name", "Field"), DISCORD_FIELD_NAME_MAX)
        value = _clip_text(f.get("value", "—"), DISCORD_FIELD_VALUE_MAX)
        safe.append({"name": name or "Field", "value": value or "—", "inline": bool(f.get("inline", False))})
    return safe


def _sanitize_embed(embed: dict[str, Any]) -> dict[str, Any]:
    out = dict(embed)
    if "title" in out:
        out["title"] = _clip_text(out.get("title"), DISCORD_TITLE_MAX)
    if "description" in out:
        out["description"] = _clip_text(out.get("description"), DISCORD_DESC_MAX)
    if "fields" in out and isinstance(out["fields"], list):
        out["fields"] = _sanitize_fields(out["fields"])
    if "footer" in out and isinstance(out["footer"], dict):
        out["footer"] = {"text": _clip_text(out["footer"].get("text", ""), DISCORD_FOOTER_MAX)}
    return out


def _discord_embed_technical(t: TechnicalSection) -> dict[str, Any]:
    """Build a fielded embed for the technical section."""
    stage = "YES" if t.stage_2 else "NO"
    vcp = "YES" if t.vcp else "NO"
    return {
        "title": f"Technical Analysis -- {t.ticker}",
        "color": 0x3498DB,
        "fields": [
            {"name": "Price", "value": f"**${t.current_price:,.2f}**", "inline": True},
            {"name": "52w Range", "value": f"${t.low_52w:,.2f} - ${t.high_52w:,.2f}\n({t.pct_from_high:.1f}% of high)", "inline": True},
            {"name": "Signal Score", "value": f"**{t.signal_score:.0f}/100**", "inline": True},
            {"name": "Moving Averages", "value": f"SMA 50: ${t.sma_50:,.2f}\nSMA 150: ${t.sma_150:,.2f}\nSMA 200: ${t.sma_200:,.2f}", "inline": True},
            {"name": "Volume", "value": f"Last: {t.last_volume:,.0f}\n50d Avg: {t.avg_vol_50:,.0f}\nATR-14: ${t.atr_14:,.2f}", "inline": True},
            {"name": "Checks", "value": f"Stage 2: **{stage}**\nVCP: **{vcp}**\nSector: **{t.sector_etf}**", "inline": True},
        ],
    }


def _discord_embed_dcf(d: DCFSection) -> dict[str, Any]:
    """Build a fielded embed for the DCF section."""
    if d.error:
        return {"title": "DCF Model", "description": d.error, "color": 0xF39C12}
    fields = [
        {"name": "Assumptions", "value": f"Growth: {d.growth_rate*100:.1f}%\nWACC: {d.wacc*100:.1f}%\nTerminal: {d.terminal_growth*100:.1f}%", "inline": True},
        {"name": "Valuation", "value": f"Intrinsic: **${d.intrinsic_value:.2f}**\nCurrent: ${d.current_price:.2f}\nMargin: **{d.margin_of_safety:+.1f}%**", "inline": True},
        {"name": "Enterprise", "value": f"EV: ${d.enterprise_value/1e9:.2f}B\nNet Debt: ${d.net_debt/1e9:.2f}B\nShares: {d.shares_outstanding/1e9:.2f}B", "inline": True},
    ]
    if d.sensitivity:
        rows = []
        for row in d.sensitivity:
            v1 = f"${row.get('tg_0.02', 0) or 0:.0f}" if row.get('tg_0.02') else "N/A"
            v2 = f"${row.get('tg_0.025', 0) or 0:.0f}" if row.get('tg_0.025') else "N/A"
            v3 = f"${row.get('tg_0.03', 0) or 0:.0f}" if row.get('tg_0.03') else "N/A"
            rows.append(f"{row['wacc']*100:.0f}%: {v1} / {v2} / {v3}")
        fields.append({"name": "Sensitivity (2%/2.5%/3% TG)", "value": "\n".join(rows), "inline": False})
    return {"title": "DCF Model", "color": 0x2ECC71, "fields": fields}


def _discord_embed_comps(c: CompsSection) -> dict[str, Any]:
    """Build a fielded embed for comparable analysis."""
    if c.error:
        return {"title": f"Comparable Analysis -- {c.ticker}", "description": c.error, "color": 0xF39C12}
    fields = []
    for p in c.peers[:6]:
        pe = f"{p['pe']:.1f}" if p.get("pe") else "N/A"
        ps = f"{p['ps']:.1f}" if p.get("ps") else "N/A"
        mc = f"${p['market_cap']/1e9:.1f}B" if p.get("market_cap") else "N/A"
        fields.append({
            "name": p["ticker"],
            "value": f"P/E: {pe}\nP/S: {ps}\nCap: {mc}",
            "inline": True,
        })
    impl_parts = []
    if c.implied_price_pe > 0:
        impl_parts.append(f"P/E -> **${c.implied_price_pe:.2f}**")
    if c.implied_price_ps > 0:
        impl_parts.append(f"P/S -> **${c.implied_price_ps:.2f}**")
    desc = f"Median P/E: **{c.median_pe:.1f}** | P/S: **{c.median_ps:.1f}** | EV/EBITDA: **{c.median_ev_ebitda:.1f}**"
    if impl_parts:
        desc += "\nImplied: " + " | ".join(impl_parts)
    return {"title": f"Comparable Analysis -- {c.ticker}", "description": desc, "color": 0x9B59B6, "fields": fields}


def _discord_embed_health(h: HealthSection) -> dict[str, Any]:
    """Build a fielded embed for financial health."""
    if h.error:
        return {"title": "Financial Health", "description": h.error, "color": 0xF39C12}
    fields = [
        {"name": "Current Ratio", "value": f"**{h.current_ratio:.2f}**", "inline": True},
        {"name": "Debt/Equity", "value": f"**{h.debt_to_equity:.2f}**", "inline": True},
        {"name": "Interest Coverage", "value": f"**{h.interest_coverage:.1f}x**", "inline": True},
        {"name": "ROE", "value": f"**{h.roe*100:.1f}%**", "inline": True},
        {"name": "Op. Margin", "value": f"**{h.operating_margin*100:.1f}%**", "inline": True},
    ]
    if h.flags:
        fields.append({"name": f"Flags ({len(h.flags)})", "value": "\n".join(f"- {f}" for f in h.flags), "inline": False})
    else:
        fields.append({"name": "Flags", "value": "No red flags detected.", "inline": True})
    return {"title": "Financial Health", "color": 0xF39C12, "fields": fields}


def _discord_embed_edgar(e: EdgarSection) -> dict[str, Any]:
    """Build a fielded embed for SEC EDGAR section."""
    if e.error:
        return {"title": "SEC Edgar", "description": e.error, "color": 0xE67E22}

    lines = [
        f"Risk: **{(e.risk_tag or 'unknown').upper()}**",
        f"Recent 8-K: **{'YES' if e.recent_8k else 'NO'}**",
        f"Latest filing age: **{e.filing_recency_days if e.filing_recency_days is not None else 'N/A'}** day(s)",
    ]
    if e.from_cache:
        lines.append("Source: cache")
    fields: list[dict[str, Any]] = [{"name": "SEC Snapshot", "value": "\n".join(lines), "inline": False}]

    if e.risk_reasons:
        fields.append({
            "name": "Risk Notes",
            "value": "\n".join(f"- {r}" for r in e.risk_reasons[:5]),
            "inline": False,
        })

    if e.recent_filings:
        filing_lines = []
        for f in e.recent_filings[:6]:
            form = f.get("form", "?")
            dt = f.get("date", "?")
            desc = _clip_text(f.get("description", ""), 70)
            url = f.get("url", "")
            filing_lines.append(f"**{form}** ({dt}) - [{desc}]({url})")
        fields.append({"name": "Recent Filings", "value": "\n".join(filing_lines), "inline": False})
    else:
        fields.append({"name": "Recent Filings", "value": "No recent target filings found.", "inline": False})

    cik = e.cik or "Unknown"
    return {"title": f"SEC Edgar - CIK {cik}", "color": 0xE67E22, "fields": fields}


def _discord_embed_mirofish(m: MiroFishSection) -> dict[str, Any]:
    """Build a fielded embed for MiroFish section."""
    if m.error:
        return {"title": "MiroFish Simulation", "description": m.error, "color": 0xE74C3C}

    fields: list[dict[str, Any]] = [
        {
            "name": "Summary",
            "value": _clip_text(m.summary or "No summary available.", 700),
            "inline": False,
        },
        {
            "name": "Core Metrics",
            "value": (
                f"Conviction: **{m.conviction_score:+d}/100**\n"
                f"Continuation: **{m.continuation_probability:.0%}**\n"
                f"Bull trap: **{m.bull_trap_probability:.0%}**"
            ),
            "inline": True,
        },
    ]
    if m.agent_votes:
        lines = []
        for v in m.agent_votes[:5]:
            name = str(v.get("name", "?"))[:24]
            score = int(v.get("score", 0) or 0)
            reason = _clip_text(v.get("reason", ""), 90)
            lines.append(f"**{name}** ({score:+d}) - {reason}")
        fields.append({"name": "Top Agent Votes", "value": "\n".join(lines), "inline": False})
    return {"title": "MiroFish Simulation", "color": 0xE74C3C, "fields": fields}


REPORT_SECTION_MAP = {
    "tech": "technical",
    "technical": "technical",
    "dcf": "dcf",
    "comps": "comps",
    "health": "health",
    "edgar": "edgar",
    "mirofish": "mirofish",
}


def report_to_discord_sections(
    report: FullReport,
    section_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Convert a FullReport to a list of Discord embed-ready dicts.
    Uses embed fields for mobile-friendly rendering. Timestamps included.
    If section_filter is set, returns only that section.
    """
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()

    builders: list[tuple[str, Any, Any]] = [
        ("technical", report.technical, _discord_embed_technical),
        ("dcf", report.dcf, _discord_embed_dcf),
        ("comps", report.comps, _discord_embed_comps),
        ("health", report.health, _discord_embed_health),
        ("edgar", report.edgar, _discord_embed_edgar),
        ("mirofish", report.mirofish, _discord_embed_mirofish),
    ]

    resolved_filter = REPORT_SECTION_MAP.get((section_filter or "").lower().strip()) if section_filter else None

    embeds = []
    for key, data, builder in builders:
        if data is None:
            continue
        if resolved_filter and key != resolved_filter:
            continue

        embed = builder(data)
        embed = _sanitize_embed(embed)
        embed["timestamp"] = ts
        embeds.append(embed)

    if report.synthesis and not resolved_filter:
        embeds.append({
            "title": f"Synthesis -- {report.ticker}",
            "description": _clip_text(report.synthesis, DISCORD_DESC_MAX),
            "color": 0x1ABC9C,
            "timestamp": ts,
            "footer": {"text": "Use /check TICKER for a quick verdict"},
        })

    return embeds


def send_report_to_discord(report: FullReport) -> bool:
    """Send all report sections to Discord as webhook embeds."""
    import requests as req

    env = _load_env()
    webhook_url = env.get("DISCORD_WEBHOOK_URL", os.environ.get("DISCORD_WEBHOOK_URL", "")).strip()
    if not webhook_url:
        LOG.warning("DISCORD_WEBHOOK_URL not set -- cannot send report to Discord.")
        return False

    embeds = report_to_discord_sections(report)

    for i in range(0, len(embeds), 10):
        batch = embeds[i:i + 10]
        payload = {"embeds": batch}
        try:
            resp = req.post(webhook_url, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            LOG.error("Discord send failed: %s", e)
            return False
        if i + 10 < len(embeds):
            time.sleep(1)

    return True


def quick_check(ticker: str, auth: Any = None, skill_dir: Path | None = None) -> dict[str, Any]:
    """
    Fast 3-line verdict for a ticker. Returns a single Discord embed dict.
    No MiroFish, no EDGAR -- just technicals + DCF + comps + health in ~5 seconds.
    """
    from datetime import datetime, timezone

    sd = _resolve_skill_dir(skill_dir)
    ticker = ticker.upper().strip()

    try:
        from market_data import get_daily_history
        df = get_daily_history(ticker, days=400, auth=auth, skill_dir=sd)
    except Exception:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            raw = t.history(period="2y", auto_adjust=True)
            df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
            df = df[["open", "high", "low", "close", "volume"]].sort_index()
        except Exception:
            df = pd.DataFrame()

    tech = _build_technical(ticker, df, auth, sd)
    dcf = _build_dcf(ticker)
    health = _build_health(ticker)

    if tech.stage_2 and tech.vcp and tech.signal_score >= 60:
        verdict = "STRONG SETUP"
        color = 0x2ECC71
    elif tech.stage_2:
        verdict = "WATCH"
        color = 0xF39C12
    else:
        verdict = "HOLD OFF"
        color = 0xE74C3C

    price_str = f"${tech.current_price:,.2f}" if tech.current_price else ""

    embed: dict[str, Any] = {
        "title": f"Quick Check | {ticker} {price_str}",
        "description": f"**{ticker}** -- **{verdict}**",
        "color": color,
        "fields": [
            {
                "name": "Technical",
                "value": (
                    f"Stage 2: **{'YES' if tech.stage_2 else 'NO'}**\n"
                    f"VCP: **{'YES' if tech.vcp else 'NO'}**\n"
                    f"Score: **{tech.signal_score:.0f}/100**"
                ),
                "inline": True,
            },
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Want more? Use /report {ticker}"},
    }

    fund_parts = []
    if dcf and not dcf.error and dcf.intrinsic_value > 0:
        label = "undervalued" if dcf.margin_of_safety > 0 else "overvalued"
        fund_parts.append(f"DCF: **${dcf.intrinsic_value:.0f}** ({label})")
    if health and not health.error:
        n_flags = len(health.flags)
        fund_parts.append(f"Health: **{n_flags} flag(s)**" if n_flags else "Health: **OK**")

    if fund_parts:
        embed["fields"].append({
            "name": "Fundamentals",
            "value": "\n".join(fund_parts),
            "inline": True,
        })
    else:
        embed["fields"].append({
            "name": "Fundamentals",
            "value": "Unavailable",
            "inline": True,
        })

    return embed


def report_to_json(report: FullReport) -> str:
    """Serialize FullReport to JSON."""
    data = asdict(report)
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Full Financial Report Generator")
    parser.add_argument("ticker", help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument("--discord", action="store_true", help="Send report to Discord webhook")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of markdown")
    parser.add_argument("--skip-mirofish", action="store_true", help="Skip MiroFish simulation (faster)")
    parser.add_argument("--skip-edgar", action="store_true", help="Skip SEC EDGAR lookup")
    parser.add_argument(
        "--record-hypothesis",
        action="store_true",
        help="Append a hypothesis ledger record when HYPOTHESIS_LEDGER_ENABLED=true",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    report = generate_full_report(
        args.ticker,
        skip_mirofish=args.skip_mirofish,
        skip_edgar=args.skip_edgar,
    )

    if args.record_hypothesis:
        try:
            from config import get_hypothesis_ledger_enabled
            from hypothesis_ledger import append_hypothesis, record_from_report_conclusion

            if get_hypothesis_ledger_enabled(SKILL_DIR):
                tech = report.technical
                direction = "long"
                if tech and not tech.stage_2:
                    direction = "neutral"
                ref_px = float(tech.current_price) if tech and tech.current_price else None
                conclusion = {
                    "direction": direction,
                    "reference_px": ref_px,
                    "summary": (report.synthesis or "")[:800],
                    "sections_touched": [
                        s
                        for s, sec in (
                            ("technical", tech),
                            ("dcf", report.dcf),
                            ("comps", report.comps),
                            ("health", report.health),
                            ("edgar", report.edgar),
                            ("mirofish", report.mirofish),
                        )
                        if sec
                    ],
                }
                append_hypothesis(
                    record_from_report_conclusion(
                        args.ticker.upper(),
                        conclusion,
                        skill_dir=SKILL_DIR,
                    ),
                    skill_dir=SKILL_DIR,
                )
                print("[OK] Hypothesis record appended.")
            else:
                print("[SKIP] HYPOTHESIS_LEDGER_ENABLED is not true.")
        except Exception as e:
            print(f"[WARN] Hypothesis record failed: {e}")

    if args.json:
        print(report_to_json(report))
    else:
        print(report_to_markdown(report))

    if args.discord:
        ok = send_report_to_discord(report)
        if ok:
            print("\n[OK] Report sent to Discord.")
        else:
            print("\n[FAIL] Failed to send to Discord (check DISCORD_WEBHOOK_URL).")


if __name__ == "__main__":
    main()
