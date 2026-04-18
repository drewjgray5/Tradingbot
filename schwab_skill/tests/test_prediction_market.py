from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prediction_market import (
    PredictionMarketConfig,
    PredictionMarketOverlayEngine,
    PredictionMarketSnapshot,
    apply_overlay_to_signal,
    build_prediction_market_config,
    load_historical_provider,
)


def _cfg(*, mode: str = "live") -> PredictionMarketConfig:
    return PredictionMarketConfig(
        enabled=True,
        mode=mode,
        provider="stub",
        timeout_ms=1000,
        cache_ttl_sec=30,
        max_event_age_hours=24.0,
        min_liquidity=500.0,
        max_spread=0.05,
        min_match_confidence=0.0,
        score_delta_clamp=2.0,
        size_mult_min=0.9,
        size_mult_max=1.1,
        advisory_delta_clamp=0.02,
    )


class _StaticProvider:
    provider_name = "test_provider"

    def __init__(self, snapshot: PredictionMarketSnapshot | None) -> None:
        self._snapshot = snapshot

    def lookup_event(self, *, ticker: str, as_of: datetime) -> PredictionMarketSnapshot | None:
        return self._snapshot


class _ErrorProvider:
    provider_name = "error_provider"

    def lookup_event(self, *, ticker: str, as_of: datetime) -> PredictionMarketSnapshot | None:
        raise TimeoutError("simulated timeout")


def test_prediction_market_config_defaults(tmp_path) -> None:
    cfg = build_prediction_market_config(skill_dir=tmp_path)
    assert cfg.enabled is False
    assert cfg.mode == "off"
    assert cfg.provider == "stub"
    assert cfg.timeout_ms == 1200
    assert cfg.cache_ttl_sec == 300
    assert cfg.max_event_age_hours == 24.0
    assert cfg.min_liquidity == 1000.0
    assert cfg.max_spread == 0.08
    assert cfg.min_match_confidence == 0.55
    assert cfg.score_delta_clamp == 2.0
    assert cfg.size_mult_min == 0.9
    assert cfg.size_mult_max == 1.1
    assert cfg.advisory_delta_clamp == 0.02


def test_prediction_market_config_strict_parsing(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PRED_MARKET_ENABLED=maybe",
                "PRED_MARKET_MODE=invalid",
                "PRED_MARKET_PROVIDER=bad",
                "PRED_MARKET_TIMEOUT_MS=not-a-number",
                "PRED_MARKET_CACHE_TTL_SEC=-1",
                "PRED_MARKET_MAX_EVENT_AGE_HOURS=nope",
                "PRED_MARKET_MIN_LIQUIDITY=abc",
                "PRED_MARKET_MAX_SPREAD=xyz",
            ]
        )
    )
    cfg = build_prediction_market_config(skill_dir=tmp_path)
    assert cfg.enabled is False
    assert cfg.mode == "off"
    assert cfg.provider == "stub"
    assert cfg.timeout_ms == 1200
    assert cfg.cache_ttl_sec == 10
    assert cfg.max_event_age_hours == 24.0
    assert cfg.min_liquidity == 1000.0
    assert cfg.max_spread == 0.08
    assert cfg.min_match_confidence == 0.55


def test_prediction_market_stale_event_excluded() -> None:
    as_of = datetime(2026, 4, 15, 15, 0, tzinfo=timezone.utc)
    snapshot = PredictionMarketSnapshot(
        event_id="ev1",
        event_name="AAPL beats estimates",
        implied_probability=0.71,
        liquidity=20000.0,
        spread=0.01,
        volume=50000.0,
        resolution_ts=as_of + timedelta(days=3),
        updated_ts=as_of - timedelta(hours=48),
        provider="test_provider",
    )
    cfg = _cfg(mode="live")
    cfg.max_event_age_hours = 12.0
    engine = PredictionMarketOverlayEngine(config=cfg, provider=_StaticProvider(snapshot))
    evaluation = engine.evaluate(ticker="AAPL", as_of=as_of, regime_is_bullish=True)
    assert evaluation.status == "skipped"
    assert evaluation.reason == "stale_event"
    assert "stale_event" in evaluation.exclusion_reasons


def test_prediction_market_shadow_vs_live_overlay_behavior() -> None:
    as_of = datetime(2026, 4, 15, 15, 0, tzinfo=timezone.utc)
    snapshot = PredictionMarketSnapshot(
        event_id="ev2",
        event_name="NVDA product cycle",
        implied_probability=0.78,
        liquidity=30000.0,
        spread=0.01,
        volume=120000.0,
        resolution_ts=as_of + timedelta(days=7),
        updated_ts=as_of - timedelta(minutes=15),
        provider="test_provider",
    )
    base_signal = {"ticker": "NVDA", "signal_score": 62.0}
    shadow_engine = PredictionMarketOverlayEngine(
        config=PredictionMarketConfig(
            enabled=_cfg(mode="shadow").enabled,
            mode=_cfg(mode="shadow").mode,
            provider=_cfg(mode="shadow").provider,
            timeout_ms=_cfg(mode="shadow").timeout_ms,
            cache_ttl_sec=_cfg(mode="shadow").cache_ttl_sec,
            max_event_age_hours=_cfg(mode="shadow").max_event_age_hours,
            min_liquidity=_cfg(mode="shadow").min_liquidity,
            max_spread=_cfg(mode="shadow").max_spread,
            min_match_confidence=_cfg(mode="shadow").min_match_confidence,
            score_delta_clamp=_cfg(mode="shadow").score_delta_clamp,
            size_mult_min=_cfg(mode="shadow").size_mult_min,
            size_mult_max=_cfg(mode="shadow").size_mult_max,
            advisory_delta_clamp=_cfg(mode="shadow").advisory_delta_clamp,
        ),
        provider=_StaticProvider(snapshot),
    )
    live_engine = PredictionMarketOverlayEngine(
        config=_cfg(mode="live"),
        provider=_StaticProvider(snapshot),
    )

    shadow_eval = shadow_engine.evaluate(ticker="NVDA", as_of=as_of, regime_is_bullish=True)
    shadow_out = apply_overlay_to_signal(signal=base_signal, evaluation=shadow_eval, advisory=None)
    assert shadow_eval.status == "ok"
    assert shadow_eval.overlay["applied"] is False
    assert float(shadow_out["signal_score"]) == 62.0
    assert float(shadow_out["prediction_market_size_multiplier"]) == 1.0

    live_eval = live_engine.evaluate(ticker="NVDA", as_of=as_of, regime_is_bullish=True)
    live_out = apply_overlay_to_signal(signal=base_signal, evaluation=live_eval, advisory=None)
    assert live_eval.status == "ok"
    assert live_eval.overlay["applied"] is True
    assert float(live_out["signal_score"]) > 62.0
    assert float(live_out["prediction_market_size_multiplier"]) > 1.0


def test_prediction_market_provider_timeout_fails_safe() -> None:
    as_of = datetime(2026, 4, 15, 15, 0, tzinfo=timezone.utc)
    engine = PredictionMarketOverlayEngine(
        config=PredictionMarketConfig(
            enabled=_cfg(mode="live").enabled,
            mode=_cfg(mode="live").mode,
            provider=_cfg(mode="live").provider,
            timeout_ms=250,
            cache_ttl_sec=_cfg(mode="live").cache_ttl_sec,
            max_event_age_hours=_cfg(mode="live").max_event_age_hours,
            min_liquidity=_cfg(mode="live").min_liquidity,
            max_spread=_cfg(mode="live").max_spread,
            min_match_confidence=_cfg(mode="live").min_match_confidence,
            score_delta_clamp=_cfg(mode="live").score_delta_clamp,
            size_mult_min=_cfg(mode="live").size_mult_min,
            size_mult_max=_cfg(mode="live").size_mult_max,
            advisory_delta_clamp=_cfg(mode="live").advisory_delta_clamp,
        ),
        provider=_ErrorProvider(),
    )
    evaluation = engine.evaluate(ticker="MSFT", as_of=as_of, regime_is_bullish=True)
    assert evaluation.status == "error"
    assert evaluation.reason == "provider_error:TimeoutError"
    assert evaluation.overlay["applied"] is False


def test_historical_provider_point_in_time_selection(tmp_path) -> None:
    path = tmp_path / "pm_history.json"
    path.write_text(
        """
[
  {
    "ticker": "AAPL",
    "event_id": "ev_a1",
    "event_name": "AAPL up this week",
    "implied_probability": 0.60,
    "updated_ts": "2026-04-10T12:00:00Z",
    "resolution_ts": "2026-04-20T16:00:00Z",
    "liquidity": 5000,
    "spread": 0.02,
    "volume": 10000
  },
  {
    "ticker": "AAPL",
    "event_id": "ev_a2",
    "event_name": "AAPL up this week",
    "implied_probability": 0.80,
    "updated_ts": "2026-04-12T12:00:00Z",
    "resolution_ts": "2026-04-20T16:00:00Z",
    "liquidity": 5000,
    "spread": 0.02,
    "volume": 10000
  }
]
""".strip()
    )
    provider = load_historical_provider(path)
    snap_early = provider.lookup_event(
        ticker="AAPL",
        as_of=datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
    )
    snap_late = provider.lookup_event(
        ticker="AAPL",
        as_of=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
    )
    assert snap_early is not None
    assert snap_late is not None
    assert float(snap_early.implied_probability) == 0.60
    assert float(snap_late.implied_probability) == 0.80
