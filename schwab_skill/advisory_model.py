"""
Advisory probability model for signal ranking (Phase 1).

Phase 1 target:
  P(up over next 10 trading days), advisory-only.

This module provides:
- Canonical feature/label schema for offline dataset generation
- Walk-forward training + calibration utilities
- Runtime inference on live scanner signals
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stage_analysis import add_indicators, check_vcp_volume, compute_signal_components, is_stage_2

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_FILE = "advisory_model_v1.json"
SECTOR_LOOKBACK_DAYS = 21
MIN_BARS = 260

# Canonical feature columns for Phase 1 model.
FEATURE_COLUMNS: list[str] = [
    "signal_score",
    "pct_from_52w_high",
    "avg_vcp_volume_ratio",
    "close_vs_sma50_pct",
    "close_vs_sma200_pct",
    "atr_pct",
    "ret_5d_prev",
    "ret_20d_prev",
    "volume_ratio",
    "sector_rel_21d",
    "sec_risk_score",
    "miro_continuation_prob",
    "miro_bull_trap_prob",
]
INTERACTION_FEATURE_COLUMNS: list[str] = [
    "signal_score_x_sector_rel_21d",
    "signal_score_x_volume_ratio",
    "ret_20d_prev_x_sector_rel_21d",
    "atr_pct_x_volume_ratio",
]

# Labels logged now for multi-target Phase 2.
LABEL_COLUMNS: list[str] = [
    "y_up_5d",
    "y_up_10d",
    "y_return_bucket_10d",
    "y_drawdown_gt5_10d",
    "ret_5d_fwd",
    "ret_10d_fwd",
    "drawdown_10d",
]

_MODEL_CACHE: dict[str, Any] = {"path": None, "artifact": None}


@dataclass
class AdvisoryPrediction:
    p_up_10d: float
    p_up_10d_raw: float
    confidence_bucket: str
    model_version: str
    expected_move_10d: float
    feature_coverage: float
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "p_up_10d": round(float(self.p_up_10d), 4),
            "p_up_10d_raw": round(float(self.p_up_10d_raw), 4),
            "confidence_bucket": self.confidence_bucket,
            "model_version": self.model_version,
            "expected_move_10d": round(float(self.expected_move_10d), 4),
            "feature_coverage": round(float(self.feature_coverage), 3),
            "reasoning": self.reasoning[:220],
        }


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _risk_tag_to_score(tag: str | None) -> float:
    t = (tag or "").strip().lower()
    if t == "low":
        return 1.0
    if t == "medium":
        return 0.0
    if t == "high":
        return -1.0
    return 0.0


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception as e:
        raise RuntimeError("yfinance is required for advisory dataset generation") from e

    for attempt in range(3):
        try:
            raw = yf.Ticker(symbol).history(start=start_date, end=end_date, auto_adjust=True)
            out = raw.rename(
                columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            out = out[[c for c in ("open", "high", "low", "close", "volume") if c in out.columns]].copy()
            if out.empty:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
            for col in ("open", "high", "low", "close", "volume"):
                if col not in out.columns:
                    out[col] = 0.0
            out = out[["open", "high", "low", "close", "volume"]].astype(float)
            out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
            out.index.name = "date"
            return out.sort_index().drop_duplicates()
        except Exception:
            if attempt < 2:
                time.sleep(1.0 + attempt)
                continue
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")


def _window_return(df: pd.DataFrame, idx: int, lookback: int) -> float | None:
    if idx < lookback or idx >= len(df):
        return None
    start_px = float(df["close"].iloc[idx - lookback])
    end_px = float(df["close"].iloc[idx])
    if start_px <= 0:
        return None
    return (end_px - start_px) / start_px


def _sector_relative_return(
    ticker_df: pd.DataFrame,
    ticker_idx: int,
    sector_df: pd.DataFrame | None,
    spy_df: pd.DataFrame | None,
) -> float:
    if sector_df is None or spy_df is None:
        return 0.0
    ticker_date = ticker_df.index[ticker_idx]
    try:
        sector_i = sector_df.index.get_indexer([ticker_date], method="pad")[0]
        spy_i = spy_df.index.get_indexer([ticker_date], method="pad")[0]
    except Exception:
        return 0.0
    if sector_i < 0 or spy_i < 0:
        return 0.0
    s_ret = _window_return(sector_df, sector_i, SECTOR_LOOKBACK_DAYS)
    p_ret = _window_return(spy_df, spy_i, SECTOR_LOOKBACK_DAYS)
    if s_ret is None or p_ret is None:
        return 0.0
    return float(s_ret - p_ret)


def _extract_row_features(
    window: pd.DataFrame,
    signal_score: float,
    components: dict[str, Any],
    sector_rel_21d: float,
    sec_risk_tag: str | None = None,
    miro_result: dict[str, Any] | None = None,
) -> dict[str, float]:
    latest = window.iloc[-1]
    price = _safe_float(latest.get("close"), 0.0)
    sma_50 = _safe_float(latest.get("sma_50"), 0.0)
    sma_200 = _safe_float(latest.get("sma_200"), 0.0)
    atr_14 = _safe_float(latest.get("atr_14"), 0.0)
    latest_vol = _safe_float(latest.get("volume"), 0.0)
    avg_vol_50 = _safe_float(latest.get("avg_vol_50"), 0.0)
    c5 = _safe_float(window["close"].iloc[-6], price) if len(window) >= 6 else price
    c20 = _safe_float(window["close"].iloc[-21], price) if len(window) >= 21 else price

    miro = miro_result or {}
    return {
        "signal_score": float(signal_score),
        "pct_from_52w_high": _safe_float(components.get("pct_from_52w_high"), 0.0),
        "avg_vcp_volume_ratio": _safe_float(components.get("avg_vcp_volume_ratio"), 1.0),
        "close_vs_sma50_pct": ((price / sma_50) - 1.0) if sma_50 > 0 else 0.0,
        "close_vs_sma200_pct": ((price / sma_200) - 1.0) if sma_200 > 0 else 0.0,
        "atr_pct": (atr_14 / price) if price > 0 else 0.0,
        "ret_5d_prev": ((price / c5) - 1.0) if c5 > 0 else 0.0,
        "ret_20d_prev": ((price / c20) - 1.0) if c20 > 0 else 0.0,
        "volume_ratio": (latest_vol / avg_vol_50) if avg_vol_50 > 0 else 1.0,
        "sector_rel_21d": float(sector_rel_21d),
        "sec_risk_score": _risk_tag_to_score(sec_risk_tag),
        "miro_continuation_prob": _safe_float(miro.get("continuation_probability"), 0.5),
        "miro_bull_trap_prob": _safe_float(miro.get("bull_trap_probability"), 0.5),
    }


def _add_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["signal_score_x_sector_rel_21d"] = (
        pd.to_numeric(out.get("signal_score"), errors="coerce").fillna(0.0)
        * pd.to_numeric(out.get("sector_rel_21d"), errors="coerce").fillna(0.0)
    )
    out["signal_score_x_volume_ratio"] = (
        pd.to_numeric(out.get("signal_score"), errors="coerce").fillna(0.0)
        * pd.to_numeric(out.get("volume_ratio"), errors="coerce").fillna(0.0)
    )
    out["ret_20d_prev_x_sector_rel_21d"] = (
        pd.to_numeric(out.get("ret_20d_prev"), errors="coerce").fillna(0.0)
        * pd.to_numeric(out.get("sector_rel_21d"), errors="coerce").fillna(0.0)
    )
    out["atr_pct_x_volume_ratio"] = (
        pd.to_numeric(out.get("atr_pct"), errors="coerce").fillna(0.0)
        * pd.to_numeric(out.get("volume_ratio"), errors="coerce").fillna(0.0)
    )
    return out


def _future_labels(df: pd.DataFrame, idx: int) -> dict[str, Any] | None:
    if idx + 10 >= len(df):
        return None
    close_t = _safe_float(df["close"].iloc[idx], 0.0)
    close_5 = _safe_float(df["close"].iloc[idx + 5], close_t)
    close_10 = _safe_float(df["close"].iloc[idx + 10], close_t)
    if close_t <= 0:
        return None
    ret_5 = (close_5 - close_t) / close_t
    ret_10 = (close_10 - close_t) / close_t
    lows = df["close"].iloc[idx + 1 : idx + 11].astype(float)
    dd = float((lows.min() - close_t) / close_t) if len(lows) else 0.0
    bucket = 0
    if ret_10 <= -0.02:
        bucket = -1
    elif ret_10 >= 0.02:
        bucket = 1
    return {
        "y_up_5d": int(ret_5 > 0),
        "y_up_10d": int(ret_10 > 0),
        "y_return_bucket_10d": int(bucket),
        "y_drawdown_gt5_10d": int(dd <= -0.05),
        "ret_5d_fwd": float(ret_5),
        "ret_10d_fwd": float(ret_10),
        "drawdown_10d": float(dd),
    }


def build_advisory_dataset(
    skill_dir: Path | str | None = None,
    tickers: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_tickers: int | None = None,
) -> pd.DataFrame:
    """
    Build canonical advisory dataset.

    Dataset is intentionally scanner-parity leaning:
    rows are emitted only when Stage2 and VCP conditions are true.
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d")
    universe: list[str]
    if tickers:
        universe = [str(t).strip().upper() for t in tickers if str(t).strip()]
    else:
        from signal_scanner import _load_watchlist

        universe = _load_watchlist(skill_dir)
    universe = list(dict.fromkeys(universe))
    if max_tickers and max_tickers > 0:
        universe = universe[: max_tickers]

    from config import get_breakout_confirm_enabled
    from sector_strength import SECTOR_ETFS, get_ticker_sector_etf

    sector_perf: dict[str, pd.DataFrame] = {}
    for sym in sorted(set(SECTOR_ETFS + ["SPY"])):
        sdf = _fetch_history(sym, start, end)
        if not sdf.empty and len(sdf) >= MIN_BARS:
            sector_perf[sym] = sdf

    breakout_enabled = bool(get_breakout_confirm_enabled(skill_dir))
    rows: list[dict[str, Any]] = []

    for ticker in universe:
        df_raw = _fetch_history(ticker, start, end)
        if df_raw.empty or len(df_raw) < MIN_BARS:
            continue
        df = add_indicators(df_raw)
        try:
            sector_etf = get_ticker_sector_etf(ticker, skill_dir=skill_dir)
        except Exception:
            sector_etf = None
        etf_df = sector_perf.get(sector_etf) if sector_etf else None
        spy_df = sector_perf.get("SPY")

        for i in range(200, len(df) - 10):
            window = df.iloc[: i + 1].copy()
            if not is_stage_2(window, skill_dir):
                continue
            if not check_vcp_volume(window, skill_dir):
                continue
            if breakout_enabled and i >= 1 and _safe_float(df["close"].iloc[i]) < _safe_float(df["high"].iloc[i - 1]):
                continue

            labels = _future_labels(df, i)
            if labels is None:
                continue

            components = compute_signal_components(window)
            score = _safe_float(components.get("score"), 0.0)
            sector_rel = _sector_relative_return(df, i, etf_df, spy_df)
            feat = _extract_row_features(
                window=window,
                signal_score=score,
                components=components,
                sector_rel_21d=sector_rel,
                sec_risk_tag=None,
                miro_result=None,
            )
            entry_date = pd.Timestamp(df.index[i]).isoformat()
            rows.append(
                {
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "sector_etf": sector_etf,
                    "breakout_confirmed": int(
                        i < 1 or (_safe_float(df["close"].iloc[i]) >= _safe_float(df["high"].iloc[i - 1]))
                    ),
                    **feat,
                    **labels,
                }
            )
    if not rows:
        return pd.DataFrame(columns=["ticker", "entry_date", "sector_etf", "breakout_confirmed", *FEATURE_COLUMNS, *LABEL_COLUMNS])
    out = pd.DataFrame(rows)
    out.sort_values(["entry_date", "ticker"], inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def _prepare_matrix(df: pd.DataFrame, feature_cols: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x_df = df[feature_cols].copy()
    for c in feature_cols:
        x_df[c] = pd.to_numeric(x_df[c], errors="coerce")
        med = float(x_df[c].median()) if x_df[c].notna().any() else 0.0
        x_df[c] = x_df[c].fillna(med)
    x = x_df.to_numpy(dtype=np.float64)
    mu = x.mean(axis=0)
    sigma = x.std(axis=0)
    sigma[sigma < 1e-9] = 1.0
    xz = (x - mu) / sigma
    return xz, mu, sigma


def _fit_logistic_l2(
    xz: np.ndarray,
    y: np.ndarray,
    l2: float = 0.05,
    lr: float = 0.05,
    epochs: int = 800,
) -> tuple[np.ndarray, float]:
    n, d = xz.shape
    w = np.zeros(d, dtype=np.float64)
    b = 0.0
    if n == 0:
        return w, b
    for _ in range(epochs):
        p = _sigmoid(xz @ w + b)
        err = p - y
        grad_w = (xz.T @ err) / n + (l2 * w)
        grad_b = float(err.mean())
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def _predict_prob(xz: np.ndarray, w: np.ndarray, b: float) -> np.ndarray:
    return _sigmoid(xz @ w + b)


def _fit_calibration_bins(raw_prob: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict[str, float]]:
    if len(raw_prob) == 0:
        return []
    q = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.quantile(raw_prob, q)
    edges[0] = 0.0
    edges[-1] = 1.0
    bins: list[dict[str, float]] = []
    for i in range(n_bins):
        lo = float(edges[i])
        hi = float(edges[i + 1])
        if i == n_bins - 1:
            mask = (raw_prob >= lo) & (raw_prob <= hi)
        else:
            mask = (raw_prob >= lo) & (raw_prob < hi)
        count = int(mask.sum())
        if count == 0:
            continue
        p_mean = float(raw_prob[mask].mean())
        y_mean = float(y[mask].mean())
        bins.append(
            {
                "lo": lo,
                "hi": hi,
                "p_mean": p_mean,
                "y_mean": y_mean,
                "count": float(count),
            }
        )
    return bins


def _calibration_monotonicity(
    bins: list[dict[str, float]],
    tolerance: float = 0.03,
) -> dict[str, float | int | bool]:
    if not bins:
        return {
            "is_monotonic": False,
            "violations": 0,
            "worst_drop": 0.0,
            "max_allowed_drop": float(tolerance),
        }
    ys = [float(b.get("y_mean", 0.0)) for b in bins]
    violations = 0
    worst_drop = 0.0
    prev = ys[0]
    for y in ys[1:]:
        drop = prev - y
        if drop > tolerance:
            violations += 1
            worst_drop = max(worst_drop, drop)
        prev = y
    return {
        "is_monotonic": violations == 0,
        "violations": int(violations),
        "worst_drop": round(float(worst_drop), 6),
        "max_allowed_drop": float(tolerance),
    }


def _apply_calibration_bins(raw_p: float, bins: list[dict[str, float]]) -> float:
    if not bins:
        return float(raw_p)
    p = float(max(0.0, min(1.0, raw_p)))
    chosen = None
    for i, b in enumerate(bins):
        lo = float(b.get("lo", 0.0))
        hi = float(b.get("hi", 1.0))
        in_bin = (p >= lo and p <= hi) if i == len(bins) - 1 else (p >= lo and p < hi)
        if in_bin:
            chosen = b
            break
    if chosen is None:
        chosen = min(bins, key=lambda b: abs(float(b.get("p_mean", 0.5)) - p))
    y_mean = float(chosen.get("y_mean", p))
    count = float(chosen.get("count", 0.0))
    weight = max(0.2, min(1.0, count / 80.0))
    return float(max(0.0, min(1.0, weight * y_mean + (1.0 - weight) * p)))


def _binary_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    eps = 1e-8
    p = np.clip(p, eps, 1.0 - eps)
    yb: np.ndarray = y.astype(np.float64)
    brier = float(np.mean((p - yb) ** 2))
    logloss = float(-np.mean(yb * np.log(p) + (1.0 - yb) * np.log(1.0 - p)))
    pred = (p >= 0.5).astype(np.int32)
    acc = float((pred == yb).mean())
    tp = float(((pred == 1) & (yb == 1)).sum())
    fp = float(((pred == 1) & (yb == 0)).sum())
    fn = float(((pred == 0) & (yb == 1)).sum())
    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    top = p >= np.quantile(p, 0.8) if len(p) >= 5 else np.ones_like(p, dtype=bool)
    top_hit = float(yb[top].mean()) if top.any() else 0.0

    # AUC via rank method.
    order = np.argsort(p)
    ranks = np.empty_like(order)
    ranks[order] = np.arange(1, len(p) + 1)
    pos = yb == 1
    n_pos = int(pos.sum())
    n_neg = int((~pos).sum())
    if n_pos > 0 and n_neg > 0:
        auc = float((ranks[pos].sum() - (n_pos * (n_pos + 1) / 2.0)) / (n_pos * n_neg))
    else:
        auc = 0.5

    return {
        "samples": float(len(y)),
        "positive_rate": float(yb.mean()) if len(yb) else 0.0,
        "brier": brier,
        "logloss": logloss,
        "accuracy": acc,
        "precision": precision,
        "recall": recall,
        "auc": auc,
        "top20_hit_rate": top_hit,
    }


def _classify_regime(test_df: pd.DataFrame) -> str:
    if test_df.empty:
        return "unknown"
    ret20 = float(pd.to_numeric(test_df.get("ret_20d_prev"), errors="coerce").median())
    atr = float(pd.to_numeric(test_df.get("atr_pct"), errors="coerce").median())
    if ret20 >= 0.03 and atr <= 0.025:
        return "risk_on"
    if ret20 <= -0.02 or atr >= 0.03:
        return "risk_off"
    return "neutral"


def _generate_walk_forward_folds(df: pd.DataFrame, profile: str = "standard") -> list[dict[str, str]]:
    if df.empty:
        return []
    dts = pd.to_datetime(df["entry_date"]).sort_values()
    start = dts.min()
    end = dts.max()
    if profile == "promotion":
        train_years = 3
        val_months = 3
        test_months = 3
        step_months = 3
    else:
        train_years = 3
        val_months = 6
        test_months = 6
        step_months = 6
    folds: list[dict[str, str]] = []
    cursor = start + pd.DateOffset(years=train_years)
    while cursor + pd.DateOffset(months=val_months + test_months) <= end:
        train_end = cursor
        val_end = train_end + pd.DateOffset(months=val_months)
        test_end = val_end + pd.DateOffset(months=test_months)
        folds.append(
            {
                "train_end": pd.Timestamp(train_end).isoformat(),
                "val_end": pd.Timestamp(val_end).isoformat(),
                "test_end": pd.Timestamp(test_end).isoformat(),
            }
        )
        cursor = cursor + pd.DateOffset(months=step_months)
    return folds


def _candidate_metric_rank(metrics: dict[str, float]) -> tuple[float, float, float]:
    # Higher is better in lexicographic order:
    # 1) AUC 2) top20 hit-rate 3) lower Brier (negated)
    return (
        float(metrics.get("auc", 0.0)),
        float(metrics.get("top20_hit_rate", 0.0)),
        -float(metrics.get("brier", 1.0)),
    )


def train_advisory_model(
    dataset: pd.DataFrame,
    target_col: str = "y_up_10d",
    profile: str = "standard",
    allow_model_upgrades: bool = False,
) -> dict[str, Any]:
    if dataset.empty:
        raise ValueError("Dataset is empty; cannot train advisory model.")
    if target_col not in dataset.columns:
        raise ValueError(f"Missing target column: {target_col}")
    df = dataset.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df.sort_values("entry_date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df[target_col] = pd.to_numeric(df[target_col], errors="coerce").fillna(0).astype(int)

    df = _add_interaction_features(df)
    folds_cfg = _generate_walk_forward_folds(df, profile=profile)
    fold_metrics: list[dict[str, Any]] = []
    for idx, fold in enumerate(folds_cfg):
        tr_end = pd.Timestamp(fold["train_end"])
        va_end = pd.Timestamp(fold["val_end"])
        te_end = pd.Timestamp(fold["test_end"])
        tr = df[df["entry_date"] < tr_end]
        va = df[(df["entry_date"] >= tr_end) & (df["entry_date"] < va_end)]
        te = df[(df["entry_date"] >= va_end) & (df["entry_date"] < te_end)]
        if len(tr) < 250 or len(va) < 60 or len(te) < 60:
            continue
        ytr = tr[target_col].to_numpy(dtype=np.float64)
        yva = va[target_col].to_numpy(dtype=np.float64)
        yte = te[target_col].to_numpy(dtype=np.float64)

        candidates: list[dict[str, Any]] = [
            {"name": "baseline_logistic", "features": FEATURE_COLUMNS, "l2": 0.05}
        ]
        if allow_model_upgrades:
            candidates.append(
                {
                    "name": "interaction_logistic",
                    "features": FEATURE_COLUMNS + INTERACTION_FEATURE_COLUMNS,
                    "l2": 0.08,
                }
            )
        chosen: dict[str, Any] | None = None
        for cand in candidates:
            cols = list(cand["features"])
            xtr, mu, sigma = _prepare_matrix(tr, cols)
            w, b = _fit_logistic_l2(xtr, ytr, l2=float(cand.get("l2", 0.05)))
            xva = ((va[cols].to_numpy(dtype=np.float64) - mu) / sigma)
            pva_raw = _predict_prob(xva, w, b)
            bins = _fit_calibration_bins(pva_raw, yva, n_bins=10)
            pva = np.array([_apply_calibration_bins(float(p), bins) for p in pva_raw], dtype=np.float64)
            mva = _binary_metrics(yva, pva)
            monot = _calibration_monotonicity(bins)
            rank = _candidate_metric_rank(mva)
            payload = {
                "name": str(cand["name"]),
                "features": cols,
                "mu": mu,
                "sigma": sigma,
                "w": w,
                "b": b,
                "bins": bins,
                "rank": rank,
                "validation_metrics": mva,
                "calibration_monotonicity": monot,
            }
            if chosen is None or payload["rank"] > chosen["rank"]:
                chosen = payload

        if chosen is None:
            continue
        cols = list(chosen["features"])
        mu = chosen["mu"]
        sigma = chosen["sigma"]
        w = chosen["w"]
        b = float(chosen["b"])
        bins = list(chosen["bins"])
        xte = ((te[cols].to_numpy(dtype=np.float64) - mu) / sigma)
        pte_raw = _predict_prob(xte, w, b)
        pte = np.array([_apply_calibration_bins(float(p), bins) for p in pte_raw], dtype=np.float64)
        m = _binary_metrics(yte, pte)
        m["top10_hit_rate"] = float(yte[pte >= np.quantile(pte, 0.9)].mean()) if len(pte) >= 10 else float(m["top20_hit_rate"])
        monot_te = _calibration_monotonicity(
            _fit_calibration_bins(pte, yte, n_bins=10),
        )
        regime = _classify_regime(te)
        fold_metrics.append(
            {
                "fold_idx": idx + 1,
                "train_rows": int(len(tr)),
                "val_rows": int(len(va)),
                "test_rows": int(len(te)),
                "metrics": m,
                "model_selected": str(chosen["name"]),
                "feature_count": len(cols),
                "regime": regime,
                "validation_metrics": chosen["validation_metrics"],
                "calibration_monotonicity": monot_te,
            }
        )

    split_idx = max(1, int(len(df) * 0.85))
    tr_all = df.iloc[:split_idx].copy()
    cal = df.iloc[split_idx:].copy()
    if len(cal) < 80:
        cal = tr_all.iloc[-120:].copy()
        tr_all = tr_all.iloc[:-120].copy()
    tr_all = _add_interaction_features(tr_all)
    cal = _add_interaction_features(cal)
    ytr = tr_all[target_col].to_numpy(dtype=np.float64)
    ycal = cal[target_col].to_numpy(dtype=np.float64)
    final_candidates: list[dict[str, Any]] = [
        {"name": "baseline_logistic", "features": FEATURE_COLUMNS, "l2": 0.05}
    ]
    if allow_model_upgrades:
        final_candidates.append(
            {"name": "interaction_logistic", "features": FEATURE_COLUMNS + INTERACTION_FEATURE_COLUMNS, "l2": 0.08}
        )
    chosen_final: dict[str, Any] | None = None
    for cand in final_candidates:
        cols = list(cand["features"])
        xtr, mu, sigma = _prepare_matrix(tr_all, cols)
        w, b = _fit_logistic_l2(xtr, ytr, l2=float(cand.get("l2", 0.05)))
        xcal = ((cal[cols].to_numpy(dtype=np.float64) - mu) / sigma)
        pcal_raw = _predict_prob(xcal, w, b)
        bins = _fit_calibration_bins(pcal_raw, ycal, n_bins=10)
        pcal = np.array([_apply_calibration_bins(float(p), bins) for p in pcal_raw], dtype=np.float64)
        met = _binary_metrics(ycal, pcal)
        met["top10_hit_rate"] = (
            float(ycal[pcal >= np.quantile(pcal, 0.9)].mean()) if len(pcal) >= 10 else float(met["top20_hit_rate"])
        )
        rank = _candidate_metric_rank(met)
        payload = {
            "name": str(cand["name"]),
            "features": cols,
            "mu": mu,
            "sigma": sigma,
            "w": w,
            "b": b,
            "bins": bins,
            "metrics": met,
            "rank": rank,
            "calibration_monotonicity": _calibration_monotonicity(bins),
        }
        if chosen_final is None or payload["rank"] > chosen_final["rank"]:
            chosen_final = payload
    if chosen_final is None:
        raise ValueError("Could not train any advisory model candidate.")
    mu = chosen_final["mu"]
    sigma = chosen_final["sigma"]
    w = chosen_final["w"]
    b = float(chosen_final["b"])
    bins = list(chosen_final["bins"])
    cal_metrics = dict(chosen_final["metrics"])
    cal_monot = chosen_final["calibration_monotonicity"]

    agg = {}
    if fold_metrics:
        keys = ["brier", "logloss", "accuracy", "precision", "recall", "auc", "top20_hit_rate"]
        for k in keys:
            vals = [float(f["metrics"][k]) for f in fold_metrics]
            agg[k] = {
                "mean": round(float(np.mean(vals)), 6),
                "std": round(float(np.std(vals)), 6),
            }
        top10_vals = [float(f["metrics"].get("top10_hit_rate", f["metrics"]["top20_hit_rate"])) for f in fold_metrics]
        agg["top10_hit_rate"] = {
            "mean": round(float(np.mean(top10_vals)), 6),
            "std": round(float(np.std(top10_vals)), 6),
        }
        regimes = [str(f.get("regime", "unknown")) for f in fold_metrics]
        agg["regime_counts"] = {r: regimes.count(r) for r in sorted(set(regimes))}

    avg_abs_move = float(np.abs(df["ret_10d_fwd"].astype(float)).mean()) if "ret_10d_fwd" in df.columns else 0.03
    if profile == "promotion":
        acceptance_gates = {
            "min_fold_count": 6,
            "min_auc": 0.52,
            "min_fold_auc": 0.52,
            "max_fold_auc_std": 0.05,
            "max_brier": 0.255,
            "min_top20_hit_rate": 0.52,
            "min_top10_hit_rate_per_fold": 0.52,
            "min_regime_count": 2,
            "max_calibration_violations": 1,
            "max_calibration_worst_drop": 0.08,
        }
    else:
        acceptance_gates = {
            "min_fold_count": 2,
            "max_brier": 0.255,
            "min_auc": 0.52,
            "min_top20_hit_rate": 0.52,
        }
    artifact = {
        "model_type": "logistic_l2_with_bin_calibration",
        "model_version": f"phase1-pup10d-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "training_profile": profile,
        "allow_model_upgrades": bool(allow_model_upgrades),
        "model_selected": str(chosen_final["name"]),
        "target": target_col,
        "feature_columns": list(chosen_final["features"]),
        "base_feature_columns": FEATURE_COLUMNS,
        "interaction_feature_columns": INTERACTION_FEATURE_COLUMNS,
        "label_columns": LABEL_COLUMNS,
        "feature_mu": [float(x) for x in mu.tolist()],
        "feature_sigma": [float(x) for x in sigma.tolist()],
        "coef": [float(x) for x in w.tolist()],
        "intercept": float(b),
        "calibration_bins": bins,
        "calibration_monotonicity": cal_monot,
        "training_summary": {
            "rows_total": int(len(df)),
            "rows_train": int(len(tr_all)),
            "rows_calibration": int(len(cal)),
            "date_min": pd.Timestamp(df["entry_date"].min()).isoformat(),
            "date_max": pd.Timestamp(df["entry_date"].max()).isoformat(),
            "avg_abs_move_10d": round(avg_abs_move, 6),
            "positive_rate": round(float(df[target_col].mean()), 6),
        },
        "walk_forward": {
            "fold_count": int(len(fold_metrics)),
            "folds": fold_metrics,
            "summary": agg,
        },
        "calibration_metrics": cal_metrics,
        "acceptance_gates": acceptance_gates,
        "trained_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    return artifact


def _model_path(skill_dir: Path, override: str | None = None) -> Path:
    if override:
        p = Path(override)
        return p if p.is_absolute() else (skill_dir / p)
    from config import get_advisory_model_path

    raw = get_advisory_model_path(skill_dir)
    p = Path(raw)
    return p if p.is_absolute() else (skill_dir / p)


def _model_candidate_paths(skill_dir: Path, override: str | None = None) -> list[Path]:
    """
    Candidate advisory model locations in priority order.

    Keeps existing ADVISORY_MODEL_PATH behavior first, but falls back to the
    common repository locations to avoid silent "no advisory" when a deploy
    references an outdated relative path.
    """
    from config import get_advisory_model_path

    candidates: list[Path] = []

    def _add(p: Path) -> None:
        if p not in candidates:
            candidates.append(p)

    # Highest priority: explicit override argument.
    if override:
        p = Path(override)
        _add(p if p.is_absolute() else (skill_dir / p))

    # Next: configured env/model path.
    raw = get_advisory_model_path(skill_dir)
    cfg = Path(raw)
    _add(cfg if cfg.is_absolute() else (skill_dir / cfg))

    # Compatibility fallbacks used by prior deploy layouts.
    _add(skill_dir / DEFAULT_MODEL_FILE)
    _add(skill_dir / "artifacts" / DEFAULT_MODEL_FILE)
    return candidates


def save_model_artifact(artifact: dict[str, Any], skill_dir: Path | str | None = None, path: str | None = None) -> Path:
    skill_dir = Path(skill_dir or SKILL_DIR)
    out = _model_path(skill_dir, override=path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    _MODEL_CACHE["path"] = str(out)
    _MODEL_CACHE["artifact"] = artifact
    return out


def load_model_artifact(skill_dir: Path | str | None = None, path: str | None = None) -> dict[str, Any] | None:
    skill_dir = Path(skill_dir or SKILL_DIR)
    for model_path in _model_candidate_paths(skill_dir, override=path):
        if _MODEL_CACHE.get("path") == str(model_path) and isinstance(_MODEL_CACHE.get("artifact"), dict):
            return _MODEL_CACHE["artifact"]
        if not model_path.exists():
            continue
        try:
            data = json.loads(model_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or "coef" not in data:
            continue
        _MODEL_CACHE["path"] = str(model_path)
        _MODEL_CACHE["artifact"] = data
        return data
    return None


def _confidence_bucket(p: float, skill_dir: Path) -> str:
    from config import get_advisory_confidence_high, get_advisory_confidence_low

    high = float(get_advisory_confidence_high(skill_dir))
    low = float(get_advisory_confidence_low(skill_dir))
    if p >= high:
        return "high"
    if p >= low:
        return "medium"
    return "low"


def score_signal_advisory(
    signal: dict[str, Any],
    skill_dir: Path | str | None = None,
) -> AdvisoryPrediction | None:
    skill_dir = Path(skill_dir or SKILL_DIR)
    from config import get_advisory_model_enabled

    if not get_advisory_model_enabled(skill_dir):
        return None
    model = load_model_artifact(skill_dir)
    if not model:
        return None
    cols = list(model.get("feature_columns") or FEATURE_COLUMNS)
    mu = np.array(model.get("feature_mu") or [0.0] * len(cols), dtype=np.float64)
    sigma = np.array(model.get("feature_sigma") or [1.0] * len(cols), dtype=np.float64)
    sigma[sigma < 1e-9] = 1.0
    w = np.array(model.get("coef") or [0.0] * len(cols), dtype=np.float64)
    b = float(model.get("intercept", 0.0))
    bins = list(model.get("calibration_bins") or [])

    components = signal.get("score_components") or {}
    feature_map = _extract_row_features(
        window=pd.DataFrame(
            [
                {
                    "close": _safe_float(signal.get("price"), 0.0),
                    "sma_50": _safe_float(signal.get("sma_50"), 0.0),
                    "sma_200": _safe_float(signal.get("sma_200"), 0.0),
                    "atr_14": _safe_float((signal.get("score_components") or {}).get("atr_14"), 0.0),
                    "volume": _safe_float(signal.get("latest_volume"), 0.0),
                    "avg_vol_50": _safe_float(signal.get("avg_vol_50"), 0.0),
                }
            ]
        ),
        signal_score=_safe_float(signal.get("signal_score"), 0.0),
        components=components,
        sector_rel_21d=_safe_float(signal.get("sector_rel_21d"), 0.0),
        sec_risk_tag=str(signal.get("sec_risk_tag") or ""),
        miro_result=signal.get("mirofish_result") or {},
    )
    feature_map = _add_interaction_features(pd.DataFrame([feature_map])).iloc[0].to_dict()
    x = np.array([_safe_float(feature_map.get(c), 0.0) for c in cols], dtype=np.float64)
    coverage = float(sum(1 for c in cols if feature_map.get(c) is not None) / max(1, len(cols)))
    xz = (x - mu) / sigma
    raw = float(_sigmoid(np.array([float(xz @ w + b)], dtype=np.float64))[0])
    cal = _apply_calibration_bins(raw, bins)
    bucket = _confidence_bucket(cal, skill_dir)
    avg_abs = _safe_float((model.get("training_summary") or {}).get("avg_abs_move_10d"), 0.03)
    expected_move = float((2.0 * cal - 1.0) * avg_abs)
    reasoning = (
        f"Advisory model estimates {cal:.1%} chance of a positive 10d move; "
        f"confidence={bucket} based on score/structure/volume/sector context."
    )
    return AdvisoryPrediction(
        p_up_10d=cal,
        p_up_10d_raw=raw,
        confidence_bucket=bucket,
        model_version=str(model.get("model_version") or "unknown"),
        expected_move_10d=expected_move,
        feature_coverage=coverage,
        reasoning=reasoning,
    )

