"""
MiroFish-inspired market simulation engine.

Uses multi-agent swarm to run "what-if" scenarios for Stage 2 breakouts.
Spawns agent personas (Institutional Trend-Follower, Mean-Reversion Bot, Retail FOMO Trader)
and returns a Crowd Conviction Score (-100 to +100).

Seed data from Schwab market_data (OHLCV) and yfinance (news).
Uses auth patterns from auth.py / schwab_auth.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
SIMULATIONS_DIR = SKILL_DIR / "mirofish_sims"


def _load_env() -> dict:
    """Load .env following auth.py pattern."""
    path = SKILL_DIR / ".env"
    if not path.exists():
        return {}
    vals = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"\'')
    return vals


def _normalize_ohlcv_df(df):
    """Normalize column names (yfinance uses Capitals, Schwab uses lowercase)."""
    m = {"Close": "close", "Open": "open", "High": "high", "Low": "low", "Volume": "volume"}
    return df.rename(columns={k: v for k, v in m.items() if k in df.columns})


def _get_seed_from_df(df, ticker: str, skill_dir: Path | None = None) -> str:
    """
    Build seed material from OHLCV DataFrame (Schwab: open,high,low,close,volume; yfinance: Open,Close,...).
    Includes concrete numeric Stage2/VCP-derived features when available.
    """
    from config import get_vcp_days

    df = _normalize_ohlcv_df(df)
    if df.empty or len(df) < 5:
        return f"No sufficient price data for {ticker}."

    # Keep computations safe even if some columns are missing.
    close_s = df["close"].astype(float) if "close" in df.columns else None
    high_s = df["high"].astype(float) if "high" in df.columns else None
    vol_s = df["volume"].astype(float) if "volume" in df.columns else None

    last = df.iloc[-30:] if len(df) >= 30 else df
    if close_s is None or vol_s is None:
        end_px = float(last["close"].iloc[-1]) if "close" in last.columns else 0.0
        return f"Ticker: {ticker}\nLatest close: ${end_px:.2f}\nStage 2 breakout detected (limited features available)."

    close_30 = last["close"].astype(float)
    vol_30 = last["volume"].astype(float)
    start_px = float(close_30.iloc[0])
    end_px = float(close_30.iloc[-1])
    ret_pct = 100.0 * (end_px - start_px) / start_px if start_px else 0.0
    avg_vol_30 = float(vol_30.mean()) if len(vol_30) else 0.0
    last_vol = float(vol_30.iloc[-1]) if len(vol_30) else 0.0
    vol_ratio = last_vol / avg_vol_30 if avg_vol_30 else 1.0
    high_30 = float(close_30.max()) if len(close_30) else end_px
    low_30 = float(close_30.min()) if len(close_30) else end_px

    # SMA / avg volume features (use precomputed columns when present; else rolling fallback).
    sma_50 = float(df["sma_50"].iloc[-1]) if "sma_50" in df.columns and len(df["sma_50"].dropna()) else None
    sma_200 = float(df["sma_200"].iloc[-1]) if "sma_200" in df.columns and len(df["sma_200"].dropna()) else None
    avg_vol_50 = float(df["avg_vol_50"].iloc[-1]) if "avg_vol_50" in df.columns and len(df["avg_vol_50"].dropna()) else None

    if sma_50 is None and "close" in df.columns:
        sma_50 = float(df["close"].astype(float).rolling(50, min_periods=1).mean().iloc[-1])
    if sma_200 is None and "close" in df.columns:
        sma_200 = float(df["close"].astype(float).rolling(200, min_periods=1).mean().iloc[-1])
    if avg_vol_50 is None and "volume" in df.columns:
        avg_vol_50 = float(df["volume"].astype(float).rolling(50, min_periods=1).mean().iloc[-1])

    sma50_pct_above = (end_px / sma_50 - 1.0) * 100.0 if sma_50 is not None and sma_50 > 0 else 0.0
    sma200_pct_above = (end_px / sma_200 - 1.0) * 100.0 if sma_200 is not None and sma_200 > 0 else 0.0

    # VCP dry-up metrics.
    vcp_days = int(get_vcp_days(skill_dir))
    vcp_days_eff = max(1, min(vcp_days, len(df)))
    last_vcp = df.iloc[-vcp_days_eff:].copy()
    if "avg_vol_50" not in last_vcp.columns:
        last_vcp["avg_vol_50"] = last_vcp["volume"].astype(float).rolling(50, min_periods=1).mean()
    vcp_count_below_avg = int((last_vcp["volume"].astype(float) < last_vcp["avg_vol_50"].astype(float)).sum())
    vcp_ratios: list[float] = []
    for _, row in last_vcp.iterrows():
        avg_v = row.get("avg_vol_50")
        try:
            avg_v = float(avg_v) if avg_v is not None else 0.0
        except (TypeError, ValueError):
            avg_v = 0.0
        if avg_v and avg_v > 0:
            vcp_ratios.append(float(row["volume"]) / avg_v)
    vcp_avg_volume_ratio = float(sum(vcp_ratios) / len(vcp_ratios)) if vcp_ratios else 1.0

    # 52-week proximity: use high over last 252 trading days.
    lookback_52w = min(252, len(df))
    if high_s is not None and len(df) >= 2:
        high_52w = float(df["high"].astype(float).iloc[-lookback_52w:].max())
    else:
        high_52w = 0.0
    pct_from_high_52w = (end_px / high_52w) * 100.0 if high_52w > 0 else 0.0
    dist_from_high_52w = high_52w - end_px if high_52w > 0 else 0.0

    return (
        f"Ticker: {ticker}\n"
        f"Latest close: ${end_px:.2f} | Last 30d return: {ret_pct:+.2f}%\n"
        f"30d close range: ${low_30:.2f} - ${high_30:.2f}\n"
        f"Volume: last day {last_vol:,.0f} vs 30d avg {avg_vol_30:,.0f} (ratio {vol_ratio:.2f}x)\n"
        f"SMA alignment: close vs SMA50: {sma50_pct_above:+.2f}% | close vs SMA200: {sma200_pct_above:+.2f}%\n"
        f"VCP dry-up: vcp_days={vcp_days} | count(volume < avg_vol_50)={vcp_count_below_avg}/{vcp_days_eff} | "
        f"avg(volume/avg_vol_50)={vcp_avg_volume_ratio:.3f}\n"
        f"52-week proximity: close is {pct_from_high_52w:.2f}% of 52w high (${high_52w:.2f}); distance=${dist_from_high_52w:.2f}\n"
        f"Stage2 context: price above key SMAs + VCP consolidation assumed for this scan.\n"
        f"Key framing: continuation means trend holds next 1-2 weeks; bull trap means failed breakout / reversal next 1-2 weeks."
    )


def _get_news_seed(ticker: str) -> str:
    """Fetch recent news headlines via yfinance as additional seed."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        news: list[dict[str, Any]] = getattr(t, "get_news", lambda **_kw: [])(count=5)
        if not news:
            return "No recent news available."
        lines = []
        for n in news[:5]:
            title = (n.get("title") or n.get("headline") or "").strip()
            if title:
                lines.append(f"- {title[:120]}")
        return "Recent news:\n" + "\n".join(lines) if lines else "No recent news available."
    except Exception as e:
        LOG.debug("News fetch failed for %s: %s", ticker, e)
        return "News fetch unavailable."


def _call_llm(prompt: str, system: str, env: dict) -> str:
    """Call LLM (OpenAI/Qwen) following env config."""
    api_key = (
        env.get("MIROFISH_API_KEY")
        or os.environ.get("MIROFISH_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    base_url = (env.get("LLM_BASE_URL") or os.environ.get("LLM_BASE_URL") or "").strip()
    model = env.get("LLM_MODEL_NAME") or os.environ.get("LLM_MODEL_NAME") or "gpt-4o-mini"
    if not api_key:
        LOG.warning("MIROFISH_API_KEY / OPENAI_API_KEY not set, using fallback score")
        return ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        LOG.warning("LLM call failed: %s", e)
        return ""


def _estimate_news_sentiment_proxy(news_seed_text: str) -> float:
    """
    Simple keyword-based sentiment proxy in [-1, 1].
    Used only for weighting the retail persona; schema stays stable.
    """
    if not news_seed_text:
        return 0.0
    t = news_seed_text.lower()
    pos = [
        "upgrade",
        "beats",
        "beat",
        "surge",
        "strong",
        "record",
        "outperform",
        "growth",
        "positive",
        "raises",
        "revenue",
        "demand",
        "advance",
        "uplift",
    ]
    neg = [
        "downgrade",
        "miss",
        "lawsuit",
        "negative",
        "weak",
        "plunge",
        "disappoint",
        "cut",
        "decline",
        "investigation",
    ]
    pos_count = sum(1 for w in pos if w in t)
    neg_count = sum(1 for w in neg if w in t)
    total = pos_count + neg_count
    if total <= 0:
        return 0.0
    return max(-1.0, min(1.0, (pos_count - neg_count) / total))


def compute_seed_fingerprint(seed_df, ticker: str, skill_dir: Path | None = None) -> str:
    """
    Seed fingerprint used to validate cache correctness.
    Derived from last-row timestamp, SMA relations, and VCP dry-up summary.
    """
    from config import get_vcp_days

    df = _normalize_ohlcv_df(seed_df)
    vcp_days = int(get_vcp_days(skill_dir))
    if df.empty:
        payload: dict[str, str | int | float | None] = {"ticker": ticker.upper(), "last_close_date": None, "vcp_days": vcp_days, "ratios": None}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    vcp_days_eff = max(1, min(vcp_days, len(df)))
    last_close_date = None
    try:
        if hasattr(df.index, "to_pydatetime") and len(df.index) > 0:
            last_close_date = df.index[-1].isoformat()
    except Exception:
        last_close_date = None

    close = float(df["close"].astype(float).iloc[-1]) if "close" in df.columns else 0.0

    # Rolling fallbacks if indicators not precomputed.
    if "sma_50" in df.columns and len(df["sma_50"].dropna()) > 0:
        sma_50 = float(df["sma_50"].iloc[-1])
    else:
        sma_50 = float(df["close"].astype(float).rolling(50, min_periods=1).mean().iloc[-1]) if "close" in df.columns else 0.0

    if "sma_200" in df.columns and len(df["sma_200"].dropna()) > 0:
        sma_200 = float(df["sma_200"].iloc[-1])
    else:
        sma_200 = float(df["close"].astype(float).rolling(200, min_periods=1).mean().iloc[-1]) if "close" in df.columns else 0.0

    close_sma_50_ratio = (close / sma_50) if sma_50 and sma_50 > 0 else None
    close_sma_200_ratio = (close / sma_200) if sma_200 and sma_200 > 0 else None

    # VCP dry-up metrics.
    if "avg_vol_50" in df.columns and len(df["avg_vol_50"].dropna()) > 0:
        avg_vol_series = df["avg_vol_50"].astype(float)
    else:
        avg_vol_series = df["volume"].astype(float).rolling(50, min_periods=1).mean() if "volume" in df.columns else None

    if avg_vol_series is not None and "volume" in df.columns:
        last_vcp = df.iloc[-vcp_days_eff:].copy()
        last_vcp["avg_vol_50"] = avg_vol_series.iloc[-vcp_days_eff:].values
        vcp_count_below_avg = int((last_vcp["volume"].astype(float) < last_vcp["avg_vol_50"].astype(float)).sum())
        vcp_ratios = []
        for _, row in last_vcp.iterrows():
            avg_v = float(row.get("avg_vol_50") or 0.0)
            if avg_v > 0:
                vcp_ratios.append(float(row["volume"]) / avg_v)
        vcp_avg_volume_ratio = float(sum(vcp_ratios) / len(vcp_ratios)) if vcp_ratios else 1.0
    else:
        vcp_count_below_avg = 0
        vcp_avg_volume_ratio = 1.0

    payload = {
        "ticker": ticker.upper(),
        "last_close_date": last_close_date,
        "close": round(close, 6),
        "close_sma_50_ratio": round(close_sma_50_ratio, 6) if close_sma_50_ratio is not None else None,
        "close_sma_200_ratio": round(close_sma_200_ratio, 6) if close_sma_200_ratio is not None else None,
        "vcp_count_below_avg": int(vcp_count_below_avg),
        "vcp_avg_volume_ratio": round(float(vcp_avg_volume_ratio), 6),
        "vcp_days": int(vcp_days),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_agent_response(text: str) -> dict[str, Any]:
    """
    Parse agent output as strict JSON (one blob per agent).
    If parsing fails, fall back to legacy 'CONVICTION: N' parsing.
    Always returns a valid numeric `conviction_score` in [-100, 100].
    """
    def _clamp_int(v: Any, lo: int, hi: int, default: int = 0) -> int:
        try:
            return max(lo, min(hi, int(round(float(v)))))
        except Exception:
            return default

    def _clamp_float(v: Any, lo: float, hi: float, default: float = 0.0) -> float:
        try:
            f = float(v)
            if f != f:  # NaN
                return default
            return max(lo, min(hi, f))
        except Exception:
            return default

    horizon_default = "1-2 weeks"
    txt = (text or "").strip()
    if not txt:
        return {
            "conviction_score": 0,
            "bull_trap_probability": 0.5,
            "continuation_probability": 0.5,
            "key_drivers": [],
            "vcp_alignment": 0.0,
            "sma_alignment": 0.0,
            "horizon": horizon_default,
            "reason": "",
        }

    # 1) Strict JSON attempt.
    try:
        parsed = json.loads(txt)
        if isinstance(parsed, dict):
            conviction_score = _clamp_int(parsed.get("conviction_score", 0), -100, 100, default=0)

            continuation_probability = parsed.get("continuation_probability", None)
            bull_trap_probability = parsed.get("bull_trap_probability", None)
            if continuation_probability is None:
                continuation_probability = 0.5
            if bull_trap_probability is None:
                bull_trap_probability = 0.5

            continuation_probability = _clamp_float(continuation_probability, 0.0, 1.0, default=0.5)
            bull_trap_probability = _clamp_float(bull_trap_probability, 0.0, 1.0, default=0.5)

            # If conviction_score is missing/invalid, derive from scenario probabilities.
            if parsed.get("conviction_score", None) is None:
                base = float(continuation_probability) - float(bull_trap_probability)
                conviction_score = _clamp_int(base * 100.0, -100, 100, default=0)

            key_drivers = parsed.get("key_drivers") or []
            if isinstance(key_drivers, list):
                key_drivers = [str(s).strip() for s in key_drivers if str(s).strip()]
            else:
                key_drivers = []
            key_drivers = key_drivers[:5]
            if len(key_drivers) < 2:
                key_drivers = (key_drivers + ["mixed tape", "VCP/SMA context"])[:5]

            vcp_alignment = _clamp_float(parsed.get("vcp_alignment", 0.0), -1.0, 1.0, default=0.0)
            sma_alignment = _clamp_float(parsed.get("sma_alignment", 0.0), -1.0, 1.0, default=0.0)
            horizon = str(parsed.get("horizon") or horizon_default).strip() or horizon_default

            reason = str(parsed.get("reason") or "").strip()[:200]
            if not reason:
                reason = " | ".join(key_drivers)[:200]

            return {
                "conviction_score": conviction_score,
                "bull_trap_probability": bull_trap_probability,
                "continuation_probability": continuation_probability,
                "key_drivers": key_drivers,
                "vcp_alignment": vcp_alignment,
                "sma_alignment": sma_alignment,
                "horizon": horizon,
                "reason": reason,
            }
    except Exception:
        pass

    # 2) Robust JSON extraction (still one blob).
    try:
        match = re.search(r"\{.*\}", txt, flags=re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return _parse_agent_response(json.dumps(parsed))
    except Exception:
        pass

    # 3) Legacy fallback: 'CONVICTION: N' parsing.
    score = 0
    for line in (text or "").split("\n"):
        line_u = line.strip().upper()
        if "CONVICTION:" in line_u:
            try:
                parts = line_u.split("CONVICTION:", 1)
                num_part = parts[1].strip().split()[0].replace(",", "")
                score = max(-100, min(100, int(float(num_part))))
            except (ValueError, IndexError):
                pass

    diff = float(score) / 100.0  # [-1,1]
    continuation_probability = _clamp_float(0.5 + diff / 2.0, 0.0, 1.0, default=0.5)
    bull_trap_probability = _clamp_float(0.5 - diff / 2.0, 0.0, 1.0, default=0.5)

    return {
        "conviction_score": int(score),
        "bull_trap_probability": bull_trap_probability,
        "continuation_probability": continuation_probability,
        "key_drivers": ["legacy conviction parse", "VCP/SMA context (fallback)"],
        "vcp_alignment": 0.0,
        "sma_alignment": 0.0,
        "horizon": horizon_default,
        "reason": "",
    }


# Agent system prompts (personas)
AGENT_SYSTEMS = {
    "institutional_trend": (
        "You are an Institutional Trend-Follower. You buy Stage 2 breakouts on high volume. "
        "You favor stocks with strong momentum, above all key SMAs, and volume confirmation. "
        "Return EXACTLY one strict JSON object (no markdown, no extra text). "
        "JSON must match schema: "
        "{\"conviction_score\": int, \"bull_trap_probability\": number, \"continuation_probability\": number, "
        "\"key_drivers\": [string, ...], \"vcp_alignment\": number, \"sma_alignment\": number, \"horizon\": \"1-2 weeks\"}. "
        "Scenario A is continuation next 1-2 weeks; Scenario B is bull trap/reversal next 1-2 weeks."
    ),
    "mean_reversion": (
        "You are a Mean-Reversion Bot. You look for overextended RSI and fading momentum. "
        "You are skeptical of breakouts that may be exhaustion moves. "
        "Return EXACTLY one strict JSON object (no markdown, no extra text). "
        "JSON must match schema: "
        "{\"conviction_score\": int, \"bull_trap_probability\": number, \"continuation_probability\": number, "
        "\"key_drivers\": [string, ...], \"vcp_alignment\": number, \"sma_alignment\": number, \"horizon\": \"1-2 weeks\"}. "
        "Scenario A is continuation next 1-2 weeks; Scenario B is bull trap/reversal next 1-2 weeks."
    ),
    "retail_fomo": (
        "You are a Retail FOMO Trader. You react to social sentiment and news. "
        "You chase momentum when news is positive, panic when it turns. "
        "Return EXACTLY one strict JSON object (no markdown, no extra text). "
        "JSON must match schema: "
        "{\"conviction_score\": int, \"bull_trap_probability\": number, \"continuation_probability\": number, "
        "\"key_drivers\": [string, ...], \"vcp_alignment\": number, \"sma_alignment\": number, \"horizon\": \"1-2 weeks\"}. "
        "Scenario A is continuation next 1-2 weeks; Scenario B is bull trap/reversal next 1-2 weeks."
    ),
}


class MarketSimulation:
    """
    MiroFish-inspired "what-if" simulation for Stage 2 breakouts.
    Spawns 3 agent personas and returns Crowd Conviction Score.
    """

    def __init__(
        self,
        ticker: str,
        seed_df=None,
        auth=None,
        skill_dir: Path | None = None,
        regime_is_bullish: bool | None = None,
    ):
        self.ticker = ticker.upper()
        self.seed_df = seed_df
        self.auth = auth
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self.regime_is_bullish = regime_is_bullish
        self._env = _load_env()

    def _fetch_seed_data(self) -> tuple[str, Any]:
        """Build seed material from client/market_data or provided df. Returns (seed_text, seed_df)."""
        if self.seed_df is not None and not self.seed_df.empty:
            df = _normalize_ohlcv_df(self.seed_df)
        else:
            try:
                from market_data import get_daily_history
                auth = self.auth
                if auth is None:
                    from schwab_auth import DualSchwabAuth
                    auth = DualSchwabAuth(skill_dir=self.skill_dir)
                df = get_daily_history(self.ticker, days=30, auth=auth, skill_dir=self.skill_dir)
                if df.empty:
                    import yfinance as yf
                    t = yf.Ticker(self.ticker)
                    df = _normalize_ohlcv_df(t.history(period="1mo", auto_adjust=True))
            except Exception as e:
                LOG.warning("Seed fetch failed for %s: %s", self.ticker, e)
                return f"Data fetch failed: {e}", None
        price_seed = _get_seed_from_df(df, self.ticker, self.skill_dir)
        news_seed = _get_news_seed(self.ticker)
        return f"{price_seed}\n\n{news_seed}", df

    def run(self) -> dict[str, Any]:
        """
        Run simulation. Returns dict with:
        - conviction_score: int -100 to +100
        - summary: str e.g. "Strong Continuation" / "Bull Trap"
        - agent_votes: list of {name, score, reason}
        - seed_preview: str (truncated)
        """
        seed, seed_df = self._fetch_seed_data()
        seed_fingerprint = compute_seed_fingerprint(seed_df, self.ticker, self.skill_dir) if seed_df is not None else ""

        # Heuristic sentiment proxy from the news portion in `seed` (used only for weighting).
        news_proxy = _estimate_news_sentiment_proxy(seed)

        prompt = (
            "You are evaluating a Stage 2 breakout + VCP consolidation setup.\n\n"
            f"{seed}\n\n"
            "Evaluate Scenario A (continuation next 1-2 weeks) and Scenario B (bull trap/reversal next 1-2 weeks). "
            "Return Scenario A's probability in `continuation_probability` and Scenario B's probability in `bull_trap_probability`. "
            "Return EXACTLY one strict JSON object per agent; no other text."
        )

        agent_votes: list[dict[str, Any]] = []
        parsed_agents: list[dict[str, Any]] = []

        for name, system in AGENT_SYSTEMS.items():
            text = _call_llm(prompt, system, self._env)
            parsed = _parse_agent_response(text)
            parsed["name"] = name
            parsed_agents.append(parsed)

        # Convert each agent's probabilities+alignments into a bounded conviction_score.
        # Also populate backward-compatible `score` and `reason` fields.
        for a in parsed_agents:
            cont = float(a.get("continuation_probability", 0.5))
            bull = float(a.get("bull_trap_probability", 0.5))
            base_signal = max(-1.0, min(1.0, cont - bull))  # [-1,1]
            base_score = base_signal * 100.0  # [-100,100]

            vcp_alignment = max(-1.0, min(1.0, float(a.get("vcp_alignment", 0.0))))
            sma_alignment = max(-1.0, min(1.0, float(a.get("sma_alignment", 0.0))))
            persona_component = 100.0 * (0.5 * vcp_alignment + 0.5 * sma_alignment)  # [-100,100]

            agent_score = 0.7 * base_score + 0.3 * persona_component
            agent_score_int = int(round(max(-100.0, min(100.0, agent_score))))

            key_drivers = a.get("key_drivers") or []
            if not isinstance(key_drivers, list):
                key_drivers = [str(key_drivers)]
            key_drivers = [str(s) for s in key_drivers if str(s).strip()][:5]

            reason = (a.get("reason") or "").strip()
            if not reason and key_drivers:
                reason = " | ".join(key_drivers[:3])

            agent_votes.append({
                "name": a.get("name"),
                # Backward compatible fields (Discord embed / viewer).
                "score": agent_score_int,
                "reason": reason[:200] if reason else "",
                # Strict JSON schema fields (for robustness/scoring).
                "conviction_score": agent_score_int,
                "bull_trap_probability": float(a.get("bull_trap_probability", 0.5)),
                "continuation_probability": float(a.get("continuation_probability", 0.5)),
                "key_drivers": key_drivers,
                "vcp_alignment": vcp_alignment,
                "sma_alignment": sma_alignment,
                "horizon": a.get("horizon") or "1-2 weeks",
            })

        def _get_agent(name: str) -> dict[str, Any] | None:
            for a in parsed_agents:
                if a.get("name") == name:
                    return a
            return None

        inst = _get_agent("institutional_trend") or {}
        mean = _get_agent("mean_reversion") or {}
        retail = _get_agent("retail_fomo") or {}

        inst_sma_alignment = max(-1.0, min(1.0, float(inst.get("sma_alignment", 0.0))))
        inst_weight = 1.0 + 0.9 * abs(inst_sma_alignment) + 0.3 * max(0.0, inst_sma_alignment)

        mean_bull_prob = max(0.0, min(1.0, float(mean.get("bull_trap_probability", 0.5))))
        mean_weight = 1.0 + 1.2 * mean_bull_prob

        # Retail weighted by prompt news proxy (heuristic).
        retail_sentiment = max(-1.0, min(1.0, float(news_proxy)))
        retail_weight = max(0.2, 1.0 + 0.6 * retail_sentiment)

        base_weights = {
            "institutional_trend": float(inst_weight),
            "mean_reversion": float(mean_weight),
            "retail_fomo": float(retail_weight),
        }
        try:
            from agent_intelligence import compute_vote_disagreement, resolve_dynamic_weights

            dynamic_weights, weighting_meta = resolve_dynamic_weights(
                base_weights=base_weights,
                skill_dir=self.skill_dir,
                regime_is_bullish=self.regime_is_bullish,
            )
            disagreement = compute_vote_disagreement(agent_votes)
        except Exception as e:
            LOG.debug("Dynamic weighting unavailable for %s: %s", self.ticker, e)
            dynamic_weights = {
                "institutional_trend": 1.0,
                "mean_reversion": 1.0,
                "retail_fomo": 1.0,
            }
            weighting_meta = {
                "version": 1,
                "mode": "off",
                "weights": dynamic_weights,
                "regime_bucket": "unknown",
                "applied": False,
            }
            disagreement = 0.0

        def _score_for_agent(agent_name: str) -> int:
            for v in agent_votes:
                if v.get("name") == agent_name:
                    return int(v.get("score", 0))
            return 0

        inst_score = _score_for_agent("institutional_trend")
        mean_score = _score_for_agent("mean_reversion")
        retail_score = _score_for_agent("retail_fomo")

        # Legacy weights are used unless MIROFISH_WEIGHTING_MODE=live.
        legacy_weights = {
            "institutional_trend": float(inst_weight),
            "mean_reversion": float(mean_weight),
            "retail_fomo": float(retail_weight),
        }
        use_weights = dynamic_weights if bool(weighting_meta.get("applied")) else legacy_weights
        weight_sum = sum(use_weights.values())
        if weight_sum <= 0:
            final_score_int = 0
            overall_cont = 0.5
            overall_bull = 0.5
        else:
            final_score = (
                (use_weights.get("institutional_trend", 0.0) * inst_score)
                + (use_weights.get("mean_reversion", 0.0) * mean_score)
                + (use_weights.get("retail_fomo", 0.0) * retail_score)
            ) / weight_sum
            final_score_int = int(round(max(-100.0, min(100.0, final_score))))

            def _p(agent: dict[str, Any], k: str, default: float) -> float:
                return max(0.0, min(1.0, float(agent.get(k, default))))

            inst_cont = _p(inst, "continuation_probability", 0.5)
            inst_bull = _p(inst, "bull_trap_probability", 0.5)
            mean_cont = _p(mean, "continuation_probability", 0.5)
            mean_bull = _p(mean, "bull_trap_probability", 0.5)
            retail_cont = _p(retail, "continuation_probability", 0.5)
            retail_bull = _p(retail, "bull_trap_probability", 0.5)

            overall_cont = (
                (use_weights.get("institutional_trend", 0.0) * inst_cont)
                + (use_weights.get("mean_reversion", 0.0) * mean_cont)
                + (use_weights.get("retail_fomo", 0.0) * retail_cont)
            ) / weight_sum
            overall_bull = (
                (use_weights.get("institutional_trend", 0.0) * inst_bull)
                + (use_weights.get("mean_reversion", 0.0) * mean_bull)
                + (use_weights.get("retail_fomo", 0.0) * retail_bull)
            ) / weight_sum

        diff = overall_cont - overall_bull
        if diff >= 0.25 and overall_cont >= 0.6:
            summary = "Strong Continuation"
        elif diff >= 0.10 and overall_cont >= 0.55:
            summary = "Moderate Continuation"
        elif diff <= -0.25 and overall_bull >= 0.6:
            summary = "Bull Trap"
        elif diff <= -0.10 and overall_bull >= 0.52:
            summary = "Moderate Pullback"
        else:
            summary = "Neutral / Mixed"

        sim_id = f"sim_{uuid.uuid4().hex[:12]}"
        result = {
            "simulation_id": sim_id,
            "ticker": self.ticker,
            "conviction_score": int(final_score_int),
            "summary": summary,
            "agent_votes": agent_votes,
            "mirofish_disagreement": float(disagreement),
            "agent_weighting": weighting_meta,
            # Scenario probabilities (useful for scoring & UI).
            "continuation_probability": float(overall_cont),
            "bull_trap_probability": float(overall_bull),
            "seed_fingerprint": seed_fingerprint,
            "seed_preview": seed[:500] + "..." if len(seed) > 500 else seed,
        }
        _persist_simulation(sim_id, result, self.skill_dir)
        return result


MIROFISH_CACHE_FILE = ".mirofish_cache.json"


def _persist_simulation(sim_id: str, result: dict[str, Any], skill_dir: Path | None = None) -> None:
    """Write simulation result to mirofish_sims/{sim_id}.json for viewer consumption."""
    sim_dir = (skill_dir or SKILL_DIR) / "mirofish_sims"
    try:
        sim_dir.mkdir(parents=True, exist_ok=True)
        path = sim_dir / f"{sim_id}.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        LOG.debug("Persisted simulation %s to %s", sim_id, path)
    except Exception as e:
        LOG.warning("Failed to persist simulation %s: %s", sim_id, e)


def _get_cache_path(skill_dir: Path | None) -> Path:
    return (skill_dir or SKILL_DIR) / MIROFISH_CACHE_FILE


def cache_conviction(ticker: str, result: dict, skill_dir: Path | None = None) -> None:
    """Cache conviction score in .mirofish_cache.json (separate from watchlist cache)."""
    import time
    cache_path = _get_cache_path(skill_dir)
    data: dict[str, Any] = {}
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
        except Exception:
            data = {}
    scores = dict(data.get("mirofish_scores") or {})
    scores[ticker.upper()] = {
        "conviction_score": result["conviction_score"],
        "summary": result["summary"],
        "agent_votes": result.get("agent_votes", []),
        "simulation_id": result.get("simulation_id"),
        "seed_fingerprint": result.get("seed_fingerprint"),
        "continuation_probability": result.get("continuation_probability"),
        "bull_trap_probability": result.get("bull_trap_probability"),
        "mirofish_disagreement": result.get("mirofish_disagreement"),
        "agent_weighting": result.get("agent_weighting"),
        "timestamp": time.time(),
    }
    data["mirofish_scores"] = scores
    try:
        cache_path.write_text(json.dumps(data, indent=0))
    except Exception as e:
        LOG.warning("Conviction cache write failed: %s", e)


def export_mirofish_json(result: dict[str, Any]) -> str:
    """
    Export MiroFish simulation result as formatted JSON for display or storage.
    Includes simulation_id, conviction_score, summary, and agent_votes.
    """
    export = {
        "simulation_id": result.get("simulation_id"),
        "conviction_score": result.get("conviction_score", 0),
        "summary": result.get("summary", ""),
        "agent_votes": result.get("agent_votes", []),
    }
    return json.dumps(export, indent=2)


def get_cached_conviction(
    ticker: str,
    skill_dir: Path | None = None,
    max_age_hours: float = 24,
    seed_fingerprint: str | None = None,
) -> dict | None:
    """Get cached conviction if fresh. Returns None if missing or stale."""
    import time
    cache_path = _get_cache_path(skill_dir)
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text())
        scores = data.get("mirofish_scores", {})
        entry = scores.get(ticker.upper())
        if not entry:
            return None
        age_h = (time.time() - entry.get("timestamp", 0)) / 3600
        if age_h > max_age_hours:
            return None
        if seed_fingerprint is not None:
            if entry.get("seed_fingerprint") != seed_fingerprint:
                return None
        return entry
    except Exception:
        return None
