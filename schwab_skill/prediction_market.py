from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class PredictionMarketSnapshot:
    event_id: str
    event_name: str
    implied_probability: float
    liquidity: float | None
    spread: float | None
    volume: float | None
    resolution_ts: datetime | None
    updated_ts: datetime | None
    provider: str
    snapshot_ts: datetime | None = None
    match_confidence: float | None = None


class PredictionMarketProvider(Protocol):
    provider_name: str

    def lookup_event(
        self,
        *,
        ticker: str,
        as_of: datetime,
    ) -> PredictionMarketSnapshot | None:
        ...


@dataclass(slots=True)
class PredictionMarketConfig:
    enabled: bool
    mode: str
    provider: str
    timeout_ms: int
    cache_ttl_sec: int
    max_event_age_hours: float
    min_liquidity: float
    max_spread: float
    min_match_confidence: float
    score_delta_clamp: float
    size_mult_min: float
    size_mult_max: float
    advisory_delta_clamp: float


@dataclass(slots=True)
class PredictionMarketEvaluation:
    status: str
    reason: str | None
    provider: str
    matched_event_id: str | None
    matched_event_name: str | None
    features: dict[str, float | None]
    overlay: dict[str, float | bool | str | None]
    exclusion_reasons: list[str]


class PolymarketProvider:
    provider_name = "polymarket"

    def __init__(self, *, timeout_sec: float) -> None:
        self._timeout_sec = timeout_sec

    def lookup_event(self, *, ticker: str, as_of: datetime) -> PredictionMarketSnapshot | None:
        query = urllib.parse.quote_plus(str(ticker or "").upper())
        url = (
            "https://gamma-api.polymarket.com/markets"
            f"?active=true&closed=false&limit=20&search={query}"
        )
        payload = _http_get_json(url, timeout_sec=self._timeout_sec)
        if not isinstance(payload, list):
            return None
        for row in payload:
            snap = _snapshot_from_polymarket_row(row, ticker=str(ticker or "").upper())
            if snap is not None:
                return snap
        return None


class StubProvider:
    provider_name = "stub"

    def lookup_event(self, *, ticker: str, as_of: datetime) -> PredictionMarketSnapshot | None:
        return None


class HistoricalSnapshotProvider:
    provider_name = "historical_file"

    def __init__(self, *, snapshots: list[PredictionMarketSnapshot], ticker_field: dict[str, str]) -> None:
        self._by_ticker: dict[str, list[PredictionMarketSnapshot]] = {}
        self._ticker_field = ticker_field
        for snap in snapshots:
            ticker = str(self._ticker_field.get(snap.event_id, "")).upper()
            if not ticker:
                continue
            self._by_ticker.setdefault(ticker, []).append(snap)
        for ticker, rows in self._by_ticker.items():
            rows.sort(key=lambda s: (s.updated_ts or datetime.min.replace(tzinfo=timezone.utc)))
            self._by_ticker[ticker] = rows

    def lookup_event(self, *, ticker: str, as_of: datetime) -> PredictionMarketSnapshot | None:
        rows = self._by_ticker.get(str(ticker or "").upper(), [])
        best: PredictionMarketSnapshot | None = None
        for snap in rows:
            updated_ts = snap.updated_ts
            if updated_ts is not None and updated_ts > as_of:
                continue
            if snap.resolution_ts is not None and snap.resolution_ts <= as_of:
                continue
            best = snap
        return best


def build_provider(config: PredictionMarketConfig) -> PredictionMarketProvider:
    if config.provider == "polymarket":
        return PolymarketProvider(timeout_sec=max(0.1, float(config.timeout_ms) / 1000.0))
    return StubProvider()


def load_historical_provider(file_path: Path | str) -> HistoricalSnapshotProvider:
    path = Path(file_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("historical prediction-market file must be a JSON list")
    snapshots: list[PredictionMarketSnapshot] = []
    ticker_by_event_id: dict[str, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        event_id = str(row.get("event_id") or "").strip()
        event_name = str(row.get("event_name") or "").strip()
        if not ticker or not event_id or not event_name:
            continue
        implied_probability = _safe_float(row.get("implied_probability"))
        if implied_probability is None:
            continue
        snapshot = PredictionMarketSnapshot(
            event_id=event_id,
            event_name=event_name,
            implied_probability=max(0.0, min(1.0, implied_probability)),
            liquidity=_safe_float(row.get("liquidity")),
            spread=_safe_float(row.get("spread")),
            volume=_safe_float(row.get("volume")),
            resolution_ts=_parse_timestamp(row.get("resolution_ts")),
            updated_ts=_parse_timestamp(row.get("updated_ts")),
            provider="historical_file",
            snapshot_ts=_parse_timestamp(row.get("snapshot_ts") or row.get("updated_ts")),
            match_confidence=_safe_float(row.get("match_confidence")),
        )
        snapshots.append(snapshot)
        ticker_by_event_id[event_id] = ticker
    return HistoricalSnapshotProvider(snapshots=snapshots, ticker_field=ticker_by_event_id)


class PredictionMarketOverlayEngine:
    def __init__(self, *, config: PredictionMarketConfig, provider: PredictionMarketProvider) -> None:
        self._config = config
        self._provider = provider
        self._cache: dict[str, tuple[float, PredictionMarketSnapshot | None]] = {}
        self._cache_lock = threading.Lock()

    def evaluate(
        self,
        *,
        ticker: str,
        as_of: datetime,
        regime_is_bullish: bool | None,
    ) -> PredictionMarketEvaluation:
        if not self._config.enabled or self._config.mode == "off":
            return PredictionMarketEvaluation(
                status="disabled",
                reason="feature_off",
                provider=self._provider.provider_name,
                matched_event_id=None,
                matched_event_name=None,
                features={},
                overlay=_empty_overlay(self._config.mode),
                exclusion_reasons=["feature_off"],
            )
        snap, provider_error = self._get_cached_snapshot(ticker=ticker, as_of=as_of)
        if provider_error is not None:
            return PredictionMarketEvaluation(
                status="error",
                reason=provider_error,
                provider=self._provider.provider_name,
                matched_event_id=None,
                matched_event_name=None,
                features={},
                overlay=_empty_overlay(self._config.mode),
                exclusion_reasons=[provider_error],
            )
        if snap is None:
            return PredictionMarketEvaluation(
                status="skipped",
                reason="no_match",
                provider=self._provider.provider_name,
                matched_event_id=None,
                matched_event_name=None,
                features={},
                overlay=_empty_overlay(self._config.mode),
                exclusion_reasons=["no_match"],
            )
        features, exclusion_reasons = _build_features(
            snapshot=snap,
            as_of=as_of,
            max_event_age_hours=self._config.max_event_age_hours,
            min_liquidity=self._config.min_liquidity,
            max_spread=self._config.max_spread,
            min_match_confidence=self._config.min_match_confidence,
            regime_is_bullish=regime_is_bullish,
        )
        if exclusion_reasons:
            return PredictionMarketEvaluation(
                status="skipped",
                reason=exclusion_reasons[0],
                provider=snap.provider,
                matched_event_id=snap.event_id,
                matched_event_name=snap.event_name,
                features=features,
                overlay=_empty_overlay(self._config.mode),
                exclusion_reasons=exclusion_reasons,
            )
        overlay = _build_overlay(
            mode=self._config.mode,
            implied_prob=float(features.get("pm_implied_prob") or 0.5),
            market_quality=float(features.get("pm_market_quality_score") or 0.0),
            uncertainty=float(features.get("pm_uncertainty") or 1.0),
            score_delta_clamp=self._config.score_delta_clamp,
            size_mult_min=self._config.size_mult_min,
            size_mult_max=self._config.size_mult_max,
            advisory_delta_clamp=self._config.advisory_delta_clamp,
        )
        return PredictionMarketEvaluation(
            status="ok",
            reason=None,
            provider=snap.provider,
            matched_event_id=snap.event_id,
            matched_event_name=snap.event_name,
            features=features,
            overlay=overlay,
            exclusion_reasons=[],
        )

    def _get_cached_snapshot(self, *, ticker: str, as_of: datetime) -> tuple[PredictionMarketSnapshot | None, str | None]:
        key = str(ticker or "").upper()
        now = time.time()
        with self._cache_lock:
            item = self._cache.get(key)
            if item and (now - item[0]) <= self._config.cache_ttl_sec:
                return item[1], None
        try:
            snapshot = self._provider.lookup_event(ticker=key, as_of=as_of)
            provider_error = None
        except Exception as exc:
            LOG.warning("Prediction-market provider error for %s: %s", key, exc)
            snapshot = None
            provider_error = f"provider_error:{type(exc).__name__}"
        with self._cache_lock:
            self._cache[key] = (now, snapshot)
        return snapshot, provider_error


def apply_overlay_to_signal(
    *,
    signal: dict[str, Any],
    evaluation: PredictionMarketEvaluation,
    advisory: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(signal)
    mode = str(evaluation.overlay.get("mode") or "off")
    score_delta = float(evaluation.overlay.get("score_delta") or 0.0)
    size_multiplier = float(evaluation.overlay.get("size_multiplier") or 1.0)
    result["prediction_market"] = {
        "status": evaluation.status,
        "provider": evaluation.provider,
        "mode": mode,
        "reason": evaluation.reason,
        "matched_event_id": evaluation.matched_event_id,
        "matched_event_name": evaluation.matched_event_name,
        "features": evaluation.features,
        "overlay": evaluation.overlay,
        "exclusion_reasons": evaluation.exclusion_reasons,
    }
    result["prediction_market_size_multiplier"] = round(size_multiplier, 4)
    if mode == "live" and bool(evaluation.overlay.get("applied")):
        base_score = float(result.get("signal_score") or 0.0)
        result["signal_score_pre_prediction_market"] = round(base_score, 4)
        result["signal_score"] = max(0.0, min(100.0, round(base_score + score_delta, 4)))
        if isinstance(advisory, dict):
            p_up = advisory.get("p_up_10d")
            try:
                base_p = float(p_up)
            except (TypeError, ValueError):
                base_p = None
            if base_p is not None:
                result_advisory = dict(advisory)
                advisory_delta = float(evaluation.overlay.get("advisory_delta") or 0.0)
                result_advisory["p_up_10d"] = max(0.0, min(1.0, base_p + advisory_delta))
                result_advisory["prediction_market_delta"] = advisory_delta
                result["advisory"] = result_advisory
    return result


def build_prediction_market_config(*, skill_dir: Any) -> PredictionMarketConfig:
    from config import (
        get_pred_market_advisory_delta_clamp,
        get_pred_market_cache_ttl_sec,
        get_pred_market_enabled,
        get_pred_market_max_event_age_hours,
        get_pred_market_max_spread,
        get_pred_market_min_liquidity,
        get_pred_market_min_match_confidence,
        get_pred_market_mode,
        get_pred_market_provider,
        get_pred_market_score_delta_clamp,
        get_pred_market_size_mult_max,
        get_pred_market_size_mult_min,
        get_pred_market_timeout_ms,
    )

    return PredictionMarketConfig(
        enabled=bool(get_pred_market_enabled(skill_dir)),
        mode=str(get_pred_market_mode(skill_dir)),
        provider=str(get_pred_market_provider(skill_dir)),
        timeout_ms=int(get_pred_market_timeout_ms(skill_dir)),
        cache_ttl_sec=int(get_pred_market_cache_ttl_sec(skill_dir)),
        max_event_age_hours=float(get_pred_market_max_event_age_hours(skill_dir)),
        min_liquidity=float(get_pred_market_min_liquidity(skill_dir)),
        max_spread=float(get_pred_market_max_spread(skill_dir)),
        min_match_confidence=float(get_pred_market_min_match_confidence(skill_dir)),
        score_delta_clamp=float(get_pred_market_score_delta_clamp(skill_dir)),
        size_mult_min=float(get_pred_market_size_mult_min(skill_dir)),
        size_mult_max=float(get_pred_market_size_mult_max(skill_dir)),
        advisory_delta_clamp=float(get_pred_market_advisory_delta_clamp(skill_dir)),
    )


def _build_features(
    *,
    snapshot: PredictionMarketSnapshot,
    as_of: datetime,
    max_event_age_hours: float,
    min_liquidity: float,
    max_spread: float,
    min_match_confidence: float,
    regime_is_bullish: bool | None,
) -> tuple[dict[str, float | None], list[str]]:
    reasons: list[str] = []
    p = max(0.0, min(1.0, float(snapshot.implied_probability)))
    updated_ts = snapshot.updated_ts
    if updated_ts is not None:
        age_hours = max(0.0, (as_of - updated_ts).total_seconds() / 3600.0)
        if age_hours > max_event_age_hours:
            reasons.append("stale_event")
    if snapshot.resolution_ts is not None:
        if snapshot.resolution_ts <= as_of:
            reasons.append("resolved_event")
    if snapshot.liquidity is not None and snapshot.liquidity < min_liquidity:
        reasons.append("illiquid_market")
    if snapshot.spread is not None and snapshot.spread > max_spread:
        reasons.append("wide_spread")
    match_confidence = snapshot.match_confidence
    if match_confidence is not None and float(match_confidence) < float(min_match_confidence):
        reasons.append("match_low_confidence")
    entropy = 0.0
    if 0.0 < p < 1.0:
        entropy = -((p * math.log(p, 2.0)) + ((1.0 - p) * math.log(1.0 - p, 2.0)))
    uncertainty = max(0.0, min(1.0, entropy))
    liquidity_score = _scale_up(snapshot.liquidity, lo=0.0, hi=max(min_liquidity * 5.0, 1.0))
    spread_score = _scale_down(snapshot.spread, lo=0.0, hi=max(max_spread, 0.0001))
    volume_score = _scale_up(snapshot.volume, lo=0.0, hi=max(min_liquidity * 2.0, 1.0))
    quality = max(0.0, min(1.0, (liquidity_score * 0.5) + (spread_score * 0.3) + (volume_score * 0.2)))
    horizon_days = None
    if snapshot.resolution_ts is not None:
        horizon_days = max(0.0, (snapshot.resolution_ts - as_of).total_seconds() / 86400.0)
    alignment = None
    if regime_is_bullish is not None:
        centered = (p - 0.5) * 2.0
        alignment = centered if regime_is_bullish else -centered
    features: dict[str, float | None] = {
        "pm_implied_prob": round(p, 6),
        "pm_uncertainty": round(uncertainty, 6),
        "pm_market_quality_score": round(quality, 6),
        "pm_event_horizon_days": round(horizon_days, 6) if horizon_days is not None else None,
        "pm_alignment_with_regime": round(alignment, 6) if alignment is not None else None,
        "pm_match_confidence": round(float(match_confidence), 6) if match_confidence is not None else None,
    }
    return features, reasons


def _build_overlay(
    *,
    mode: str,
    implied_prob: float,
    market_quality: float,
    uncertainty: float,
    score_delta_clamp: float,
    size_mult_min: float,
    size_mult_max: float,
    advisory_delta_clamp: float,
) -> dict[str, float | bool | str | None]:
    confidence = max(0.0, min(1.0, market_quality * (1.0 - uncertainty)))
    centered = max(-1.0, min(1.0, (implied_prob - 0.5) * 2.0))
    raw_score_delta = centered * confidence * score_delta_clamp
    score_delta = max(-score_delta_clamp, min(score_delta_clamp, raw_score_delta))
    raw_size_mult = 1.0 + (centered * confidence * 0.15)
    size_mult = max(size_mult_min, min(size_mult_max, raw_size_mult))
    advisory_delta = max(
        -advisory_delta_clamp,
        min(advisory_delta_clamp, centered * confidence * advisory_delta_clamp),
    )
    applied = mode == "live" and confidence > 0.0
    return {
        "mode": mode,
        "confidence": round(confidence, 6),
        "score_delta": round(score_delta, 6) if applied else 0.0,
        "size_multiplier": round(size_mult, 6) if applied else 1.0,
        "advisory_delta": round(advisory_delta, 6) if applied else 0.0,
        "applied": applied,
    }


def _snapshot_from_polymarket_row(row: Any, *, ticker: str) -> PredictionMarketSnapshot | None:
    if not isinstance(row, dict):
        return None
    event_id = str(row.get("id") or row.get("market_id") or "").strip()
    question = str(row.get("question") or row.get("title") or "").strip()
    if not event_id or not question:
        return None
    implied_probability = _parse_polymarket_probability(row)
    if implied_probability is None:
        return None
    liquidity = _safe_float(row.get("liquidity") or row.get("liquidityNum"))
    spread = _safe_float(row.get("spread"))
    volume = _safe_float(row.get("volume24hr") or row.get("volume") or row.get("volumeNum"))
    resolution_ts = _parse_timestamp(row.get("endDate") or row.get("closeTime"))
    updated_ts = _parse_timestamp(row.get("updatedAt") or row.get("lastTradeTime"))
    snapshot_ts = updated_ts
    match_confidence = _estimate_match_confidence(
        ticker=ticker,
        event_name=question,
        description=str(row.get("description") or ""),
    )
    return PredictionMarketSnapshot(
        event_id=event_id,
        event_name=question,
        implied_probability=implied_probability,
        liquidity=liquidity,
        spread=spread,
        volume=volume,
        resolution_ts=resolution_ts,
        updated_ts=updated_ts,
        snapshot_ts=snapshot_ts,
        match_confidence=match_confidence,
        provider="polymarket",
    )


def _estimate_match_confidence(*, ticker: str, event_name: str, description: str) -> float:
    t = str(ticker or "").strip().upper()
    if not t:
        return 0.0
    text = f"{event_name} {description}".lower()
    has_exact = t.lower() in text
    base = 0.25
    if has_exact:
        base += 0.45
    if any(k in text for k in ("earnings", "guidance", "revenue", "eps")):
        base += 0.15
    if any(k in text for k in ("this week", "this month", "quarter", "q1", "q2", "q3", "q4")):
        base += 0.1
    return max(0.0, min(1.0, base))


def _parse_polymarket_probability(row: dict[str, Any]) -> float | None:
    prices = row.get("outcomePrices")
    if isinstance(prices, list) and prices:
        try:
            return max(0.0, min(1.0, float(prices[0])))
        except (TypeError, ValueError):
            return None
    prob = row.get("probability")
    if prob is not None:
        try:
            val = float(prob)
            if val > 1.0:
                val = val / 100.0
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return None
    return None


def _http_get_json(url: str, *, timeout_sec: float) -> Any:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("network_error") from exc
    return json.loads(payload)


def _scale_up(value: float | None, *, lo: float, hi: float) -> float:
    if value is None:
        return 0.0
    if hi <= lo:
        return 0.0
    clipped = max(lo, min(hi, float(value)))
    return (clipped - lo) / (hi - lo)


def _scale_down(value: float | None, *, lo: float, hi: float) -> float:
    if value is None:
        return 0.0
    if hi <= lo:
        return 0.0
    clipped = max(lo, min(hi, float(value)))
    return 1.0 - ((clipped - lo) / (hi - lo))


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _empty_overlay(mode: str) -> dict[str, float | bool | str | None]:
    return {
        "mode": mode,
        "confidence": 0.0,
        "score_delta": 0.0,
        "size_multiplier": 1.0,
        "advisory_delta": 0.0,
        "applied": False,
    }
