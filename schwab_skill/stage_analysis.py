"""
Stage 2 logic engine for breakout setup identification.

Uses pandas (and TA-Lib if available) for SMAs.
Configurable via STAGE2_52W_PCT, STAGE2_SMA_UPWARD_DAYS, VCP_DAYS in .env.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

TRADING_DAYS_52W = 252
SMA_50 = "sma_50"
SMA_150 = "sma_150"
SMA_200 = "sma_200"
AVG_VOL_50 = "avg_vol_50"
SKILL_DIR = Path(__file__).resolve().parent

try:
    import talib
    _HAS_TALIB = True
except ImportError:
    _HAS_TALIB = False


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add 50/150/200 SMAs and 50-day avg volume."""
    df = df.copy()
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)

    if _HAS_TALIB:
        df[SMA_50] = talib.SMA(close, 50)
        df[SMA_150] = talib.SMA(close, 150)
        df[SMA_200] = talib.SMA(close, 200)
    else:
        df[SMA_50] = close.rolling(50, min_periods=1).mean()
        df[SMA_150] = close.rolling(150, min_periods=1).mean()
        df[SMA_200] = close.rolling(200, min_periods=1).mean()

    df[AVG_VOL_50] = vol.rolling(50, min_periods=1).mean()
    # ATR-14 for volatility-based sizing
    if _HAS_TALIB:
        df["atr_14"] = talib.ATR(df["high"].astype(float), df["low"].astype(float), close, 14)
    else:
        hi, lo, cl = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        tr = pd.concat([hi - lo, (hi - cl.shift(1)).abs(), (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
        df["atr_14"] = tr.rolling(14, min_periods=1).mean()
    return df


def _slope_per_step(values: pd.Series) -> float:
    """Return linear-regression slope per step for a numeric series."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_vals = [float(v) for v in values]
    y_mean = sum(y_vals) / n
    denom = sum((i - x_mean) ** 2 for i in range(n))
    if denom <= 0:
        return 0.0
    numer = sum((i - x_mean) * (y_vals[i] - y_mean) for i in range(n))
    return numer / denom


def is_stage_2(df: pd.DataFrame, skill_dir: Path | None = None) -> bool:
    """
    True only if ALL conditions hold on most recent trading day:
    1. Price > 50 SMA > 150 SMA > 200 SMA
    2. 200 SMA strictly upward for last N days (STAGE2_SMA_UPWARD_DAYS)
    3. Price within (1 - STAGE2_52W_PCT) of 52-week high
    """
    from config import get_stage2_52w_pct, get_stage2_sma_upward_days

    if df.empty or len(df) < 1:
        return False
    df = add_indicators(df)
    latest = df.iloc[-1]
    if pd.isna(latest[SMA_200]):
        return False

    price = latest["close"]
    if price <= latest[SMA_50] or latest[SMA_50] <= latest[SMA_150] or latest[SMA_150] <= latest[SMA_200]:
        return False

    n_days = get_stage2_sma_upward_days(skill_dir) + 1
    sma_200 = df[SMA_200].dropna()
    if len(sma_200) < n_days:
        return False
    last_n = sma_200.iloc[-n_days:]
    if _slope_per_step(last_n) <= 0:
        return False

    pct_min = get_stage2_52w_pct(skill_dir)
    lookback = min(TRADING_DAYS_52W, len(df))
    high_52w = df["high"].iloc[-lookback:].max()
    if price < pct_min * high_52w:
        return False
    return True


def check_vcp_volume(df: pd.DataFrame, skill_dir: Path | None = None) -> bool:
    """True if last N days each have volume below 50-day avg (VCP_DAYS)."""
    from config import get_vcp_days

    vcp_days = get_vcp_days(skill_dir)
    if df.empty or len(df) < 50 or len(df) < vcp_days:
        return False
    df = add_indicators(df)
    last_n = df.iloc[-vcp_days:]
    for _, row in last_n.iterrows():
        if row["volume"] >= row[AVG_VOL_50]:
            return False
    return True


def compute_signal_score(
    df: pd.DataFrame,
    mirofish_conviction: int | float | None = None,
    mirofish_result: dict | None = None,
    skill_dir: Path | None = None,
) -> float:
    """
    Score 0-100 for ranking signals. Higher = stronger setup.
    Components: 52w proximity, SMA strength, volume dry-up, MiroFish.
    """
    return float(
        compute_signal_components(
            df,
            mirofish_conviction=mirofish_conviction,
            mirofish_result=mirofish_result,
            skill_dir=skill_dir,
        )["score"]
        or 0.0
    )


def compute_signal_components(
    df: pd.DataFrame,
    mirofish_conviction: int | float | None = None,
    mirofish_result: dict | None = None,
    skill_dir: Path | None = None,
) -> dict[str, float | int | None]:
    """
    Structured signal score breakdown for diagnostics and quality gates.
    Returns component points plus supporting context fields.
    """
    if df.empty or len(df) < 200:
        return {
            "score": 0.0,
            "pts_52w": 0.0,
            "pts_sma": 0.0,
            "pts_volume": 0.0,
            "pts_mirofish": 0.0,
            "pct_from_52w_high": None,
            "avg_vcp_volume_ratio": None,
            "bull_trap_probability": None,
            "continuation_probability": None,
        }
    df = add_indicators(df)
    latest = df.iloc[-1]
    price = float(latest["close"])
    score = 0.0
    pts_52w = 0.0
    pts_sma = 0.0
    pts_volume = 0.0
    pts_mirofish = 0.0
    pct_from_high: float | None = None
    avg_ratio: float | None = None
    bull_prob: float | None = None
    continuation_prob: float | None = None

    # 52w proximity (0-40): closer to high = higher
    from config import get_stage2_52w_pct

    stage2_floor = float(get_stage2_52w_pct(skill_dir))
    stage2_floor = max(0.5, min(0.99, stage2_floor))
    floor_span = max(0.01, 1.0 - stage2_floor)
    lookback = min(TRADING_DAYS_52W, len(df))
    high_52w = float(df["high"].iloc[-lookback:].max())
    if high_52w > 0:
        pct_from_high = price / high_52w
        pts_52w = max(0, (pct_from_high - stage2_floor) / floor_span) * 40
        score += pts_52w

    # SMA alignment strength (0-25): how far price above each SMA
    sma200 = float(latest.get(SMA_200, 0) or 0)
    if sma200 > 0 and price > sma200:
        pts_sma = min(25, (price - sma200) / sma200 * 100)  # 1% above = 1 pt, cap 25
        score += pts_sma

    # Volume dry-up (0-20): avg of last VCP_DAYS days vs 50d avg
    from config import get_vcp_days
    vcp_days = get_vcp_days()
    last_n = df.iloc[-vcp_days:]
    vol_ratios = []
    for _, row in last_n.iterrows():
        avg_v = row.get(AVG_VOL_50)
        if avg_v and avg_v > 0:
            vol_ratios.append(float(row["volume"]) / float(avg_v))
    if vol_ratios:
        avg_ratio = sum(vol_ratios) / len(vol_ratios)
        pts_volume = max(0, 20 - avg_ratio * 20)  # lower ratio = higher score
        score += pts_volume

    # MiroFish conviction (0-15): -100..100 maps to 0..15
    if mirofish_conviction is not None:
        try:
            cv = float(mirofish_conviction)
            miropoints = max(0, (cv + 100) / 200 * 15)
            if mirofish_result:
                bt = mirofish_result.get("bull_trap_probability", None)
                cp = mirofish_result.get("continuation_probability", None)
                try:
                    bull_prob = max(0.0, min(1.0, float(bt))) if bt is not None else None
                except (TypeError, ValueError):
                    bull_prob = None
                try:
                    continuation_prob = max(0.0, min(1.0, float(cp))) if cp is not None else None
                except (TypeError, ValueError):
                    continuation_prob = None
                if bull_prob is not None:
                    # High bull-trap probability -> near-zero contribution.
                    miropoints *= max(0.0, 1.0 - bull_prob)
            pts_mirofish = miropoints
            score += pts_mirofish
        except (TypeError, ValueError):
            pass

    return {
        "score": min(100.0, round(score, 2)),
        "pts_52w": round(float(pts_52w), 2),
        "pts_sma": round(float(pts_sma), 2),
        "pts_volume": round(float(pts_volume), 2),
        "pts_mirofish": round(float(pts_mirofish), 2),
        "pct_from_52w_high": round(float(pct_from_high), 4) if pct_from_high is not None else None,
        "avg_vcp_volume_ratio": round(float(avg_ratio), 4) if avg_ratio is not None else None,
        "bull_trap_probability": round(float(bull_prob), 4) if bull_prob is not None else None,
        "continuation_probability": round(float(continuation_prob), 4) if continuation_prob is not None else None,
    }
