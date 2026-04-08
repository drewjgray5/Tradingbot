from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class StrategyPlugin(Protocol):
    plugin_name: str
    mode: str

    def evaluate(
        self,
        *,
        signal: dict[str, Any],
        candidate: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ...


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_default_strategy_plugins(
    *,
    signal: dict[str, Any],
    candidate: dict[str, Any] | None,
    pullback_mode: str,
) -> list[dict[str, Any]]:
    """
    Build strategy plugin payloads for this signal.

    The baseline breakout strategy is always live to preserve existing behavior.
    Additional strategies can run in shadow/live via mode.
    """
    plugins: list[dict[str, Any]] = []

    base_score = max(0.0, min(100.0, _safe_float(signal.get("signal_score"), 0.0)))
    plugins.append(
        {
            "name": "trend_breakout",
            "mode": "live",
            "raw_score": round(base_score, 2),
            "triggered": True,
            "meta": {"source": "existing_signal_score"},
        }
    )

    pullback = evaluate_pullback_strategy(signal=signal, candidate=candidate, mode=pullback_mode)
    if pullback is not None:
        plugins.append(pullback)
    return plugins


def evaluate_pullback_strategy(
    *,
    signal: dict[str, Any],
    candidate: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any] | None:
    mode_norm = str(mode or "off").strip().lower()
    if mode_norm == "off":
        return None

    df = candidate.get("df") if isinstance(candidate, dict) else None
    price = _safe_float(signal.get("price"), 0.0)
    sma50 = _safe_float(signal.get("sma_50"), 0.0)
    sma200 = _safe_float(signal.get("sma_200"), 0.0)
    atr14 = None
    if df is not None and "atr_14" in getattr(df, "columns", []):
        try:
            atr14 = _safe_float(df["atr_14"].iloc[-1], 0.0)
        except Exception:
            atr14 = None

    if price <= 0 or sma50 <= 0 or sma200 <= 0:
        return {
            "name": "pullback",
            "mode": mode_norm,
            "raw_score": 0.0,
            "triggered": False,
            "meta": {"reason": "missing_inputs"},
        }

    dist_to_sma50 = (price - sma50) / sma50
    trend_ok = sma50 > sma200 and price > sma200
    pullback_zone = -0.05 <= dist_to_sma50 <= 0.02
    trigger = bool(trend_ok and pullback_zone)

    # Shadow score for diagnostics/ensemble experiments only.
    proximity = max(0.0, 1.0 - (abs(dist_to_sma50) / 0.05))
    pts_proximity = proximity * 45.0
    pts_trend = 35.0 if trend_ok else 10.0
    atr_ratio = (atr14 / price) if atr14 and price > 0 else None
    pts_volatility = 20.0
    if atr_ratio is not None:
        pts_volatility = max(0.0, min(20.0, 20.0 - (atr_ratio * 500.0)))
    raw_score = max(0.0, min(100.0, pts_proximity + pts_trend + pts_volatility))

    return {
        "name": "pullback",
        "mode": mode_norm,
        "raw_score": round(raw_score, 2),
        "triggered": trigger,
        "meta": {
            "dist_to_sma50_pct": round(dist_to_sma50 * 100.0, 2),
            "trend_ok": trend_ok,
            "pullback_zone": pullback_zone,
            "atr_ratio": round(float(atr_ratio), 4) if atr_ratio is not None else None,
        },
    }


def apply_strategy_ensemble(
    *,
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    regime_v2_snapshot: dict[str, Any] | None,
    skill_dir: Path,
) -> list[dict[str, Any]]:
    from config import get_strategy_ensemble_mode, get_strategy_regime_router_mode

    ensemble_mode = get_strategy_ensemble_mode(skill_dir)
    router_mode = get_strategy_regime_router_mode(skill_dir)
    regime_bucket = str((regime_v2_snapshot or {}).get("bucket") or "unknown").lower()

    diagnostics["strategy_ensemble_mode"] = ensemble_mode
    diagnostics["strategy_router_mode"] = router_mode
    diagnostics["strategy_router_bucket"] = regime_bucket

    out: list[dict[str, Any]] = []
    for signal in signals:
        base_score = _safe_float(signal.get("signal_score"), 0.0)
        plugins = list(signal.get("strategy_plugins") or [])
        if not plugins:
            plugins = [
                {
                    "name": "trend_breakout",
                    "mode": "live",
                    "raw_score": round(base_score, 2),
                    "triggered": True,
                    "meta": {"source": "backfill"},
                }
            ]

        live_weighted: list[tuple[str, float]] = []
        shadow_weighted: list[tuple[str, float]] = []
        for plugin in plugins:
            name = str(plugin.get("name") or "unknown")
            mode = str(plugin.get("mode") or "shadow").lower()
            raw_score = _safe_float(plugin.get("raw_score"), 0.0)
            weight = _strategy_router_weight(name=name, bucket=regime_bucket, router_mode=router_mode, skill_dir=skill_dir)
            weighted = max(0.0, min(100.0, raw_score * weight))
            plugin["router_weight"] = round(weight, 3)
            plugin["weighted_score"] = round(weighted, 2)

            diagnostics["strategy_plugins_evaluated"] = int(diagnostics.get("strategy_plugins_evaluated", 0) or 0) + 1
            if name == "pullback":
                diagnostics["strategy_pullback_evaluated"] = int(diagnostics.get("strategy_pullback_evaluated", 0) or 0) + 1
                if bool(plugin.get("triggered")):
                    diagnostics["strategy_pullback_triggered"] = int(diagnostics.get("strategy_pullback_triggered", 0) or 0) + 1

            if mode == "live":
                live_weighted.append((name, weighted))
            else:
                shadow_weighted.append((name, weighted))

        live_score = base_score
        if live_weighted:
            live_score = sum(v for _, v in live_weighted) / len(live_weighted)
        shadow_score = live_score
        if live_weighted or shadow_weighted:
            all_values = [v for _, v in (live_weighted + shadow_weighted)]
            shadow_score = sum(all_values) / max(1, len(all_values))

        top_live = max(live_weighted, key=lambda x: x[1])[0] if live_weighted else "trend_breakout"
        top_shadow = max(shadow_weighted, key=lambda x: x[1])[0] if shadow_weighted else None
        diagnostics[f"strategy_live_primary_{top_live}"] = int(diagnostics.get(f"strategy_live_primary_{top_live}", 0) or 0) + 1
        if top_shadow:
            diagnostics[f"strategy_shadow_primary_{top_shadow}"] = int(
                diagnostics.get(f"strategy_shadow_primary_{top_shadow}", 0) or 0
            ) + 1

        ranked_score = live_score if ensemble_mode == "live" else base_score
        enriched = dict(signal)
        enriched["strategy_plugins"] = plugins
        enriched["ensemble_score_live"] = round(live_score, 2)
        enriched["ensemble_score_shadow"] = round(shadow_score, 2)
        enriched["ensemble_score"] = round(ranked_score, 2)
        enriched["strategy_attribution"] = {
            "top_live": top_live,
            "top_shadow": top_shadow,
            "regime_bucket": regime_bucket,
        }
        out.append(enriched)
    return out


def _strategy_router_weight(*, name: str, bucket: str, router_mode: str, skill_dir: Path) -> float:
    if router_mode == "off":
        return 1.0
    from config import (
        get_strategy_weight_breakout_high,
        get_strategy_weight_breakout_low,
        get_strategy_weight_breakout_med,
        get_strategy_weight_pullback_high,
        get_strategy_weight_pullback_low,
        get_strategy_weight_pullback_med,
    )

    b = bucket if bucket in {"high", "medium", "low"} else "medium"
    if name == "pullback":
        if b == "high":
            return float(get_strategy_weight_pullback_high(skill_dir))
        if b == "low":
            return float(get_strategy_weight_pullback_low(skill_dir))
        return float(get_strategy_weight_pullback_med(skill_dir))
    if b == "high":
        return float(get_strategy_weight_breakout_high(skill_dir))
    if b == "low":
        return float(get_strategy_weight_breakout_low(skill_dir))
    return float(get_strategy_weight_breakout_med(skill_dir))
