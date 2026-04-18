"""Backtest parity overlay for the new intelligence layers.

The shadow flags ``META_POLICY_MODE``, ``UNCERTAINTY_MODE``,
``MIROFISH_WEIGHTING_MODE``, ``EXEC_QUALITY_MODE``, ``EXIT_MANAGER_MODE`` and
``EVENT_RISK_MODE`` all currently run only against live data. Without
historical attribution the [[promotion-playbook]] gates can't be evaluated for
months. This module provides a tiny, fully self-contained overlay that the
backtest can opt into to answer "what would PnL have been if ``X`` were live?"
on years of history in minutes.

Design contract:

* Every entrypoint is a pure function of its arguments. No globals, no I/O
  beyond the same env-driven config getters the rest of the codebase already
  uses.
* When the caller passes ``mode == "off"`` (or the function is not called),
  behaviour is *byte-identical* to the legacy backtest. This guarantee is
  important: existing baselines must not move under us.
* When ``mode == "shadow"``, the overlay records what it *would* have done
  into a diagnostics dict but never actually changes a trade decision.
* When ``mode == "live"``, the overlay applies the action.

The four overlays exposed here are:

``apply_meta_policy_overlay(signal, diagnostics, *, skill_dir, mode)``
    Wraps ``agent_intelligence.apply_meta_policy_to_signal`` so the backtest
    can suppress / downsize entries based on the uncertainty score.

``evaluate_event_risk_for_backtest(*, ticker, entry_date, df, pead_info, ...)``
    PIT-safe re-implementation of ``signal_scanner.evaluate_event_risk_policy``
    that uses the **already-fetched** PEAD info from the backtest loop instead
    of looking up the current real-world earnings calendar.

``simulate_exit_with_manager(df, entry_idx, hold_days_default, stop_pct, *, skill_dir, mode)``
    Replacement for ``backtest._simulate_exit`` that adds partial take-profit,
    breakeven move, and time-stop, mirroring the live exit-manager state
    machine in ``execution.py``.

``apply_exec_quality_overlay(slippage_bps_per_side, day_volume, qty, *, skill_dir, mode)``
    Adjusts the per-side slippage assumption based on liquidity. Mirrors the
    live ``EXEC_USE_LIMIT_FOR_LIQUID`` heuristic: liquid names get a smaller
    effective spread (limit-order execution), illiquid names get a larger one.

The ``BacktestIntelligenceConfig`` dataclass plus ``resolve_overlay_modes`` is
the bridge between user CLI flags and the per-overlay functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

PluginMode = str  # "off" | "shadow" | "live"
_VALID_MODES = ("off", "shadow", "live")


@dataclass
class BacktestIntelligenceConfig:
    """Per-overlay mode resolution. Defaults to ``off`` everywhere so that
    callers must opt in explicitly. ``from_env(skill_dir)`` mirrors the live
    runtime configuration, which is what comparison runs typically want.
    """

    meta_policy: PluginMode = "off"
    event_risk: PluginMode = "off"
    exit_manager: PluginMode = "off"
    exec_quality: PluginMode = "off"
    diagnostics: dict[str, int] = field(default_factory=dict)

    @classmethod
    def all_off(cls) -> "BacktestIntelligenceConfig":
        return cls()

    @classmethod
    def all_live(cls) -> "BacktestIntelligenceConfig":
        return cls(meta_policy="live", event_risk="live", exit_manager="live", exec_quality="live")

    @classmethod
    def from_mapping(cls, mapping: dict[str, str] | None) -> "BacktestIntelligenceConfig":
        if not mapping:
            return cls.all_off()
        return cls(
            meta_policy=_normalise_mode(mapping.get("meta_policy")),
            event_risk=_normalise_mode(mapping.get("event_risk")),
            exit_manager=_normalise_mode(mapping.get("exit_manager")),
            exec_quality=_normalise_mode(mapping.get("exec_quality")),
        )

    @classmethod
    def from_env(cls, skill_dir: Path) -> "BacktestIntelligenceConfig":
        from config import (
            get_event_risk_mode,
            get_exec_quality_mode,
            get_exit_manager_mode,
            get_meta_policy_mode,
        )

        return cls(
            meta_policy=_normalise_mode(get_meta_policy_mode(skill_dir)),
            event_risk=_normalise_mode(get_event_risk_mode(skill_dir)),
            exit_manager=_normalise_mode(get_exit_manager_mode(skill_dir)),
            exec_quality=_normalise_mode(get_exec_quality_mode(skill_dir)),
        )

    def any_enabled(self) -> bool:
        return any(
            mode in ("shadow", "live")
            for mode in (self.meta_policy, self.event_risk, self.exit_manager, self.exec_quality)
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "meta_policy": self.meta_policy,
            "event_risk": self.event_risk,
            "exit_manager": self.exit_manager,
            "exec_quality": self.exec_quality,
        }


def _normalise_mode(value: Any) -> PluginMode:
    raw = str(value or "off").strip().lower()
    return raw if raw in _VALID_MODES else "off"


def _bump(diagnostics: dict[str, int], key: str, by: int = 1) -> None:
    diagnostics[key] = int(diagnostics.get(key, 0) or 0) + by


# --------------------------------------------------------------------------- #
# 1. Meta-policy overlay
# --------------------------------------------------------------------------- #


def apply_meta_policy_overlay(
    signal: dict[str, Any],
    diagnostics: dict[str, int],
    *,
    skill_dir: Path,
    mode: PluginMode,
) -> tuple[dict[str, Any], bool, float]:
    """Run the meta-policy + uncertainty layer in a backtest context.

    Returns ``(possibly_modified_signal, allow_entry, size_multiplier)``.
    ``allow_entry`` is ``False`` only when ``mode == "live"`` and the policy
    decided to suppress. In ``shadow`` mode the function still annotates the
    signal so post-hoc analysis can compare what *would* have happened.
    """
    mode = _normalise_mode(mode)
    if mode == "off":
        return signal, True, 1.0

    # ``apply_meta_policy_to_signal`` itself reads ``META_POLICY_MODE`` /
    # ``UNCERTAINTY_MODE`` to decide what to do. To honour the overlay-level
    # mode without rewiring the live path, we temporarily swap those env vars
    # for the duration of the call.
    import os

    prev_mp = os.environ.get("META_POLICY_MODE")
    prev_un = os.environ.get("UNCERTAINTY_MODE")
    try:
        os.environ["META_POLICY_MODE"] = mode
        os.environ["UNCERTAINTY_MODE"] = mode
        from agent_intelligence import apply_meta_policy_to_signal

        modified, allow = apply_meta_policy_to_signal(
            signal=signal,
            diagnostics=diagnostics,
            skill_dir=skill_dir,
        )
    except Exception as exc:
        LOG.debug("Meta-policy overlay error for %s: %s", signal.get("ticker"), exc)
        return signal, True, 1.0
    finally:
        if prev_mp is None:
            os.environ.pop("META_POLICY_MODE", None)
        else:
            os.environ["META_POLICY_MODE"] = prev_mp
        if prev_un is None:
            os.environ.pop("UNCERTAINTY_MODE", None)
        else:
            os.environ["UNCERTAINTY_MODE"] = prev_un

    size_mult = float(modified.get("meta_policy_size_multiplier") or 1.0)
    if mode == "shadow":
        # Never block in shadow mode; only annotate.
        return modified, True, 1.0
    return modified, allow, size_mult


# --------------------------------------------------------------------------- #
# 2. Event-risk overlay (PIT-safe)
# --------------------------------------------------------------------------- #


def evaluate_event_risk_for_backtest(
    *,
    ticker: str,
    entry_date: pd.Timestamp,
    pead_info: dict[str, Any] | None,
    skill_dir: Path,
    mode: PluginMode,
) -> dict[str, Any]:
    """Point-in-time event-risk evaluation suitable for the backtest loop.

    Unlike ``signal_scanner.evaluate_event_risk_policy``, which queries the
    *current* earnings calendar (a look-ahead bias in a backtest), this
    function relies on the ``pead_info`` payload that the backtest already
    fetched for the candidate. Macro blackouts are intentionally not modelled
    here because the live macro-blackout file is only forward-looking.

    Returns a dict shaped like ``evaluate_event_risk_policy`` so downstream
    diagnostics stay comparable.
    """
    mode = _normalise_mode(mode)
    earnings_distance: int | None = None
    earnings_near = False
    block_window_days = 2
    action = "block"
    downsize_factor = 0.5
    try:
        from config import (
            get_event_action,
            get_event_block_earnings_days,
            get_event_downsize_factor,
        )

        block_window_days = int(get_event_block_earnings_days(skill_dir))
        action = str(get_event_action(skill_dir) or "block").strip().lower()
        downsize_factor = float(get_event_downsize_factor(skill_dir))
    except Exception:  # pragma: no cover - config import always present in repo
        pass

    if isinstance(pead_info, dict):
        days_since = pead_info.get("days_since_earnings")
        days_until = pead_info.get("days_until_earnings")
        if isinstance(days_until, (int, float)) and days_until >= 0:
            earnings_distance = int(days_until)
        elif isinstance(days_since, (int, float)) and 0 <= days_since <= block_window_days:
            # Post-earnings drift window also flagged so back-tested entries on
            # the day-of don't ride straight through a gap.
            earnings_distance = int(days_since)

    if earnings_distance is not None and earnings_distance <= max(0, block_window_days):
        earnings_near = True

    reasons: list[str] = []
    if earnings_near:
        reasons.append(f"earnings_within_{block_window_days}d")

    return {
        "mode": mode,
        "action": action if action in {"block", "downsize"} else "block",
        "downsize_factor": max(0.10, min(1.0, downsize_factor)),
        "earnings_distance_days": earnings_distance,
        "earnings_near": earnings_near,
        "macro_blackout": False,  # Not modelled in PIT backtest.
        "flagged": bool(reasons),
        "reasons": reasons,
        "ticker": ticker,
        "entry_date": entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date),
    }


def apply_event_risk_overlay(
    *,
    policy: dict[str, Any],
    diagnostics: dict[str, int],
    mode: PluginMode,
) -> tuple[bool, float]:
    """Translate the policy dict into ``(allow_entry, size_multiplier)``.

    ``shadow`` mode never blocks but bumps shadow counters so the comparison
    script can quantify the policy's would-be effect.
    """
    mode = _normalise_mode(mode)
    if mode == "off" or not policy.get("flagged"):
        return True, 1.0
    action = str(policy.get("action") or "block").lower()
    downsize_factor = float(policy.get("downsize_factor") or 0.5)
    if mode == "shadow":
        _bump(diagnostics, "event_risk_shadow_flagged")
        if action == "block":
            _bump(diagnostics, "event_risk_shadow_would_block")
        else:
            _bump(diagnostics, "event_risk_shadow_would_downsize")
        return True, 1.0
    # mode == "live"
    _bump(diagnostics, "event_risk_live_flagged")
    if action == "block":
        _bump(diagnostics, "event_risk_live_blocked")
        return False, 0.0
    _bump(diagnostics, "event_risk_live_downsized")
    return True, max(0.10, min(1.0, downsize_factor))


# --------------------------------------------------------------------------- #
# 3. Exit-manager overlay
# --------------------------------------------------------------------------- #


def _resolve_exit_manager_settings(skill_dir: Path) -> dict[str, Any]:
    partial_r_mult = 1.5
    partial_fraction = 0.5
    breakeven_after_partial = True
    max_hold_days = 12
    try:
        from config import (
            get_exit_breakeven_after_partial,
            get_exit_max_hold_days,
            get_exit_partial_tp_fraction,
            get_exit_partial_tp_r_mult,
        )

        partial_r_mult = float(get_exit_partial_tp_r_mult(skill_dir))
        partial_fraction = float(get_exit_partial_tp_fraction(skill_dir))
        breakeven_after_partial = bool(get_exit_breakeven_after_partial(skill_dir))
        max_hold_days = int(get_exit_max_hold_days(skill_dir))
    except Exception:  # pragma: no cover
        pass
    return {
        "partial_r_mult": max(0.1, partial_r_mult),
        "partial_fraction": max(0.05, min(0.95, partial_fraction)),
        "breakeven_after_partial": breakeven_after_partial,
        "max_hold_days": max(1, max_hold_days),
    }


def simulate_exit_with_manager(
    df: pd.DataFrame,
    entry_idx: int,
    hold_days_default: int,
    stop_pct: float,
    *,
    skill_dir: Path,
    mode: PluginMode,
) -> tuple[float, pd.Timestamp, str, dict[str, Any]]:
    """Drop-in replacement for ``backtest._simulate_exit`` that models the
    live exit-manager state machine.

    Returns ``(equivalent_exit_price, exit_date, reason, info)`` where
    ``equivalent_exit_price`` reproduces the *weighted* per-share PnL of the
    multi-leg exit (so downstream cost/return math in the backtest stays
    untouched).

    Behaviour by mode:

    * ``off``   — identical to the legacy single-leg trailing+time-stop loop.
    * ``shadow`` — runs the multi-leg simulator alongside the legacy one and
      records what would have changed in ``info["shadow"]``. Returns the
      *legacy* exit so realised PnL is unchanged.
    * ``live`` — returns the multi-leg result.
    """
    mode = _normalise_mode(mode)
    legacy_exit = _legacy_simulate_exit(df, entry_idx, hold_days_default, stop_pct)
    if mode == "off":
        return legacy_exit[0], legacy_exit[1], legacy_exit[2], {"mode": "off"}

    settings = _resolve_exit_manager_settings(skill_dir)
    managed_exit = _managed_simulate_exit(
        df,
        entry_idx,
        hold_days_default=hold_days_default,
        stop_pct=stop_pct,
        partial_r_mult=settings["partial_r_mult"],
        partial_fraction=settings["partial_fraction"],
        breakeven_after_partial=settings["breakeven_after_partial"],
        max_hold_days=settings["max_hold_days"],
    )
    info = {"mode": mode, "settings": settings, "managed": managed_exit["info"]}
    if mode == "shadow":
        info["shadow"] = {
            "legacy_exit_price": legacy_exit[0],
            "managed_exit_price": managed_exit["exit_price"],
            "legacy_reason": legacy_exit[2],
            "managed_reason": managed_exit["reason"],
        }
        return legacy_exit[0], legacy_exit[1], legacy_exit[2], info
    # mode == "live"
    return managed_exit["exit_price"], managed_exit["exit_date"], managed_exit["reason"], info


def _legacy_simulate_exit(
    df: pd.DataFrame, entry_idx: int, hold_days: int, stop_pct: float
) -> tuple[float, pd.Timestamp, str]:
    """Mirror of ``backtest._simulate_exit`` so the overlay can produce a true
    no-op when ``mode == "off"`` even from outside the backtest module.
    """
    entry_price = float(df["close"].iloc[entry_idx])
    highest_close = entry_price
    last_idx = min(entry_idx + hold_days, len(df) - 1)
    for j in range(entry_idx + 1, last_idx + 1):
        px = float(df["close"].iloc[j])
        highest_close = max(highest_close, px)
        trail_stop = highest_close * (1.0 - stop_pct)
        if px <= trail_stop:
            return px, df.index[j], "trailing_stop"
    return float(df["close"].iloc[last_idx]), df.index[last_idx], "time_exit"


def _managed_simulate_exit(
    df: pd.DataFrame,
    entry_idx: int,
    *,
    hold_days_default: int,
    stop_pct: float,
    partial_r_mult: float,
    partial_fraction: float,
    breakeven_after_partial: bool,
    max_hold_days: int,
) -> dict[str, Any]:
    """Multi-leg exit simulator.

    State machine (per-day after entry, walking close-to-close):
      1. If price <= trailing_stop -> exit remaining shares at that close.
      2. If partial not yet taken and price >= partial_target -> trim
         ``partial_fraction`` of position at that close. Optionally promote
         stop to breakeven.
      3. If days_held >= effective_hold_days -> time-exit remaining at close.
    """
    entry_price = float(df["close"].iloc[entry_idx])
    risk_per_share = entry_price * stop_pct
    partial_target = entry_price + partial_r_mult * risk_per_share
    effective_hold = min(max_hold_days, hold_days_default)
    last_idx = min(entry_idx + effective_hold, len(df) - 1)

    highest_close = entry_price
    current_stop = entry_price * (1.0 - stop_pct)
    partial_done = False
    partial_price: float | None = None
    final_price: float | None = None
    final_idx: int | None = None
    reason = "time_exit"

    for j in range(entry_idx + 1, last_idx + 1):
        px = float(df["close"].iloc[j])
        highest_close = max(highest_close, px)
        trailed = highest_close * (1.0 - stop_pct)
        # Trailing stop ratchets up but never below breakeven (when armed).
        candidate_stop = max(current_stop, trailed)
        current_stop = candidate_stop

        # Stop-out wins over partial on the same day to avoid look-ahead bias.
        if px <= current_stop:
            final_price = px
            final_idx = j
            reason = "trailing_stop_after_partial" if partial_done else "trailing_stop"
            break

        if not partial_done and px >= partial_target:
            partial_done = True
            partial_price = px
            if breakeven_after_partial:
                current_stop = max(current_stop, entry_price)
            continue

    if final_price is None:
        final_price = float(df["close"].iloc[last_idx])
        final_idx = last_idx
        reason = "partial_then_time_exit" if partial_done else "time_exit"

    legs: list[dict[str, Any]] = []
    if partial_done and partial_price is not None:
        legs.append(
            {
                "leg": "partial_tp",
                "fraction": float(partial_fraction),
                "price": float(partial_price),
            }
        )
        legs.append(
            {
                "leg": "final",
                "fraction": float(1.0 - partial_fraction),
                "price": float(final_price),
            }
        )
        weighted_return = (
            partial_fraction * (partial_price - entry_price) / entry_price
            + (1.0 - partial_fraction) * (final_price - entry_price) / entry_price
        )
        equivalent_exit_price = entry_price * (1.0 + weighted_return)
    else:
        legs.append({"leg": "final", "fraction": 1.0, "price": float(final_price)})
        equivalent_exit_price = float(final_price)

    return {
        "exit_price": float(equivalent_exit_price),
        "exit_date": df.index[final_idx if final_idx is not None else last_idx],
        "reason": reason,
        "info": {
            "legs": legs,
            "partial_done": bool(partial_done),
            "partial_target": float(partial_target),
            "effective_hold_days": int(effective_hold),
            "final_stop_price": float(current_stop),
        },
    }


# --------------------------------------------------------------------------- #
# 4. Execution-quality overlay
# --------------------------------------------------------------------------- #


def apply_exec_quality_overlay(
    *,
    slippage_bps_per_side: float,
    day_volume: float,
    qty: int,
    skill_dir: Path,
    mode: PluginMode,
) -> tuple[float, dict[str, Any]]:
    """Adjust per-side slippage assumption based on liquidity profile.

    The live ``EXEC_QUALITY_MODE`` plugin prefers limit orders for liquid
    symbols (``EXEC_USE_LIMIT_FOR_LIQUID``), which empirically halves the
    effective spread paid on those names. Illiquid names get a small extra
    penalty since wide-quote market orders dominate.

    Returns ``(effective_slippage_bps, info)``. The info dict always reports
    the raw and adjusted bps so the caller (and the comparison script) can
    show the contribution.
    """
    mode = _normalise_mode(mode)
    info: dict[str, Any] = {
        "mode": mode,
        "raw_slippage_bps": float(slippage_bps_per_side),
        "effective_slippage_bps": float(slippage_bps_per_side),
        "applied": False,
        "regime": "off",
    }
    if mode == "off":
        return float(slippage_bps_per_side), info

    use_limit_for_liquid = True
    try:
        from config import get_exec_use_limit_for_liquid

        use_limit_for_liquid = bool(get_exec_use_limit_for_liquid(skill_dir))
    except Exception:  # pragma: no cover
        pass

    # Heuristic: participation = qty / day_volume. Below 0.2% participation we
    # call the name "liquid" and assume limit-order execution at half-spread.
    # Above 1% participation we're paying a real impact cost.
    participation = (float(qty) / float(day_volume)) if day_volume and day_volume > 0 else 1.0
    raw = float(slippage_bps_per_side)
    if participation <= 0.002 and use_limit_for_liquid:
        effective = raw * 0.5
        info["regime"] = "liquid_limit"
    elif participation >= 0.01:
        effective = raw * 1.5
        info["regime"] = "illiquid_market"
    else:
        effective = raw
        info["regime"] = "neutral"

    info["effective_slippage_bps"] = float(effective)
    info["participation"] = float(participation)
    if mode == "shadow":
        # Shadow: report what we would have applied but charge the original.
        info["shadow_effective_slippage_bps"] = float(effective)
        info["effective_slippage_bps"] = float(slippage_bps_per_side)
        info["applied"] = False
        return float(slippage_bps_per_side), info
    info["applied"] = True
    return float(effective), info


__all__ = [
    "BacktestIntelligenceConfig",
    "apply_event_risk_overlay",
    "apply_exec_quality_overlay",
    "apply_meta_policy_overlay",
    "evaluate_event_risk_for_backtest",
    "simulate_exit_with_manager",
]
