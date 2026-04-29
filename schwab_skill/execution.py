"""
Risk guardrails and order execution using ONLY the Account Session.

All orders pass through the Guardrail Wrapper and regime gate. On block: return
error string and send warning via notifier. On BUY: attach ATR-based trailing
stop (2.5x ATR, clamped 5-12%). On fill: success alert.
Includes sector filter and regime gate (SPY > 200 SMA).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from circuit_breaker import maybe_trip_breaker, schwab_circuit
from market_data import extract_schwab_last_price, get_current_quote
from notifier import send_alert
from schwab_auth import DualSchwabAuth

SCHWAB_BASE = "https://api.schwabapi.com"
SKILL_DIR = Path(__file__).resolve().parent

_DEFAULT_MAX_TOTAL = 500_000.0
_DEFAULT_MAX_POSITION = 50_000.0
_DEFAULT_MAX_TRADES = 20
_METRICS_FILE = "execution_safety_metrics.json"
_EXIT_MANAGER_STATE_FILE = ".exit_manager_state.json"
_EXIT_MANAGER_LOCK = threading.Lock()


def _metrics_path(skill_dir: Path) -> Path:
    return skill_dir / _METRICS_FILE


def _load_execution_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"days": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except Exception:
        pass
    return {"days": {}}


def _save_execution_metrics(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _record_execution_metric(
    skill_dir: Path,
    event: str,
    reason: str | None = None,
) -> None:
    today = date.today().isoformat()
    path = _metrics_path(skill_dir)
    data = _load_execution_metrics(path)
    days = data.setdefault("days", {})
    day_bucket = days.setdefault(today, {"events": {}, "reasons": {}})
    events = day_bucket.setdefault("events", {})
    events[event] = int(events.get(event, 0) or 0) + 1
    if reason:
        reasons = day_bucket.setdefault("reasons", {})
        key = reason.strip()[:120] or "unknown"
        reasons[key] = int(reasons.get(key, 0) or 0) + 1

    # Keep a rolling 45-day window so the metrics file stays compact.
    cutoff = date.today() - timedelta(days=45)
    stale = [k for k in days.keys() if k < cutoff.isoformat()]
    for k in stale:
        days.pop(k, None)
    _save_execution_metrics(path, data)


def get_execution_safety_summary(
    skill_dir: Path | str | None = None,
    days: int = 1,
) -> dict[str, Any]:
    skill_dir = Path(skill_dir or SKILL_DIR)
    path = _metrics_path(skill_dir)
    data = _load_execution_metrics(path)
    all_days = data.get("days", {})
    day_keys = sorted(all_days.keys())
    take = day_keys[-max(1, int(days)) :] if day_keys else []

    events: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for d in take:
        bucket = all_days.get(d, {})
        for ev, cnt in (bucket.get("events", {}) or {}).items():
            events[ev] = events.get(ev, 0) + int(cnt or 0)
        for rsn, cnt in (bucket.get("reasons", {}) or {}).items():
            reasons[rsn] = reasons.get(rsn, 0) + int(cnt or 0)

    top_reasons = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "window_days": max(1, int(days)),
        "days_present": len(take),
        "events": events,
        "top_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
    }


def _exit_manager_state_path(skill_dir: Path) -> Path:
    return skill_dir / _EXIT_MANAGER_STATE_FILE


def _load_exit_manager_state(skill_dir: Path) -> dict[str, Any]:
    path = _exit_manager_state_path(skill_dir)
    if not path.exists():
        return {"positions": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("positions"), dict):
            return data
    except Exception:
        pass
    return {"positions": {}}


def _save_exit_manager_state(skill_dir: Path, state: dict[str, Any]) -> None:
    path = _exit_manager_state_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _exit_position_key(ticker: str, entry_order_id: str) -> str:
    return f"{ticker.upper()}:{entry_order_id}"


def _load_guardrail_config(skill_dir: Path) -> tuple[float, float, int]:
    """Load guardrail limits from .env. Defaults scale for larger accounts."""
    env_path = skill_dir / ".env"
    vals = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip().strip('"\'')
    max_total = float(vals.get("MAX_TOTAL_ACCOUNT_VALUE", _DEFAULT_MAX_TOTAL))
    max_pos = float(vals.get("MAX_POSITION_PER_TICKER", _DEFAULT_MAX_POSITION))
    max_trades = int(vals.get("MAX_TRADES_PER_DAY", _DEFAULT_MAX_TRADES))
    return max_total, max_pos, max_trades


def get_position_size_usd(
    ticker: str | None = None,
    price: float | None = None,
    skill_dir: Path | str | None = None,
) -> int:
    """
    Dollar amount per position for signal-based trades.
    When VOLATILITY_SIZING_ENABLED=true and ticker/price provided, sizes by ATR
    so risk per trade is consistent (target VOLATILITY_ATR_MULT ATRs).
    Otherwise uses POSITION_SIZE_USD (default 500).
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    try:
        from config import (
            get_volatility_atr_mult,
            get_volatility_base_usd,
            get_volatility_sizing_enabled,
        )
    except ImportError:
        pass
    else:
        if get_volatility_sizing_enabled(skill_dir) and ticker and price and price > 0:
            base_usd = get_volatility_base_usd(skill_dir)
            atr_mult = get_volatility_atr_mult(skill_dir)
            try:
                from market_data import get_daily_history
                from schwab_auth import DualSchwabAuth
                from stage_analysis import add_indicators
                auth = DualSchwabAuth(skill_dir=skill_dir)
                df = get_daily_history(ticker, days=50, auth=auth, skill_dir=skill_dir)
                if not df.empty and len(df) >= 14:
                    df = add_indicators(df)
                    atr = float(df["atr_14"].iloc[-1]) if "atr_14" in df.columns else 0
                    if atr and atr > 0:
                        risk_per_share = atr * atr_mult
                        shares = base_usd / risk_per_share
                        position_usd = shares * price
                        return max(100, int(position_usd))
            except Exception:
                pass
    env_path = skill_dir / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("POSITION_SIZE_USD="):
                val = line.split("=", 1)[1].strip().strip('"\'')
                try:
                    return max(1, int(float(val)))
                except (ValueError, TypeError):
                    pass
    return 500


def _env_bool(key: str, default: bool = True) -> bool:
    from config import _get_bool
    return _get_bool(key, default)


def _get_headers(access_token: str, for_get: bool = False) -> dict:
    h = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if not for_get:
        h["Content-Type"] = "application/json"
    return h


def _load_trade_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_trade_log(path: Path, entries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)


class GuardrailWrapper:
    """
    Enforces: max account value, max per ticker, max trades/day (configurable via .env).
    Uses Account Session for orders/balances, Market Session for quotes.
    Requests positions for accurate per-ticker limits.
    """

    def __init__(self, auth: DualSchwabAuth, skill_dir: Path | str | None = None):
        self.auth = auth
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self._trade_log_path = self.skill_dir / "guardrail_trades.json"
        self._lock = threading.Lock()
        self._max_total, self._max_pos, self._max_trades = _load_guardrail_config(self.skill_dir)

    def _get_accounts(self, access_token: str) -> list[dict]:
        if not schwab_circuit.connection_stable:
            raise RuntimeError("Schwab connection unstable (circuit breaker)")
        url = f"{SCHWAB_BASE}/trader/v1/accounts"
        try:
            resp = requests.get(
                url,
                headers=_get_headers(access_token, for_get=True),
                params={"fields": "positions"},
                timeout=30,
            )
        except Exception as e:
            maybe_trip_breaker(e, schwab_circuit)
            raise
        if resp.status_code == 401 and self.auth.account_session.force_refresh():
            access_token = self.auth.get_account_token()
            try:
                resp = requests.get(
                    url,
                    headers=_get_headers(access_token, for_get=True),
                    params={"fields": "positions"},
                    timeout=30,
                )
            except Exception as e:
                maybe_trip_breaker(e, schwab_circuit)
                raise
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def _get_account_balances(self, access_token: str) -> tuple[float, dict[str, float]]:
        accounts = self._get_accounts(access_token)
        total = 0.0
        positions: dict[str, float] = {}
        for acc in accounts:
            sec = acc.get("securitiesAccount", acc)
            equity = float(sec.get("currentBalances", {}).get("equity", 0) or 0)
            cash = float(sec.get("currentBalances", {}).get("cashBalance", 0) or 0)
            total += equity if equity else cash
            for pos in sec.get("positions", []):
                sym = pos.get("instrument", {}).get("symbol", "")
                if sym:
                    mv = float(pos.get("marketValue", 0) or 0)
                    positions[sym] = positions.get(sym, 0) + mv
        return total, positions

    def _trades_today(self) -> int:
        today = date.today().isoformat()
        return sum(1 for e in _load_trade_log(self._trade_log_path) if e.get("date") == today)

    def _record_trade(self, ticker: str, order_id: str | None = None) -> None:
        with self._lock:
            log = _load_trade_log(self._trade_log_path)
            log.append({
                "date": date.today().isoformat(),
                "ticker": ticker,
                "order_id": order_id,
                "ts": datetime.utcnow().isoformat(),
            })
            _save_trade_log(self._trade_log_path, log)

    def _get_quote_price(self, ticker: str) -> float | None:
        q = get_current_quote(ticker, auth=self.auth, skill_dir=self.skill_dir)
        p = extract_schwab_last_price(q) if isinstance(q, dict) else None
        if p is not None and p > 0:
            return p
        # Fallback: yfinance when Schwab quote fails (e.g. DTE, rate limits)
        try:
            import yfinance as yf
            t = yf.Ticker(ticker.upper())
            fi = getattr(t, "fast_info", None)
            last = None
            if fi is not None:
                last = getattr(fi, "lastPrice", None) or getattr(fi, "last_price", None)
                if last is None and isinstance(fi, dict):
                    last = fi.get("lastPrice") or fi.get("last_price")
            if last is not None and float(last) > 0:
                return float(last)
        except Exception:
            pass
        return None

    def _order_instruction(self, order: dict) -> str:
        legs = order.get("orderLegCollection", [])
        if legs:
            return (legs[0].get("instruction") or "").upper()
        return ""

    def _increases_position(self, order: dict) -> bool:
        inc = ("BUY", "SELL_SHORT", "BUY_TO_OPEN", "SELL_TO_OPEN")
        inst = self._order_instruction(order)
        if inst:
            return inst in inc
        return True

    def _reduces_position(self, order: dict) -> bool:
        dec = ("SELL", "BUY_TO_CLOSE", "BUY_TO_COVER", "SELL_TO_CLOSE")
        inst = self._order_instruction(order)
        return bool(inst and inst in dec)

    def _check_guardrails(
        self,
        ticker: str,
        quantity: int | float,
        order: dict,
        order_value_usd: float | None = None,
    ) -> str | None:
        if order and self._reduces_position(order):
            _record_execution_metric(self.skill_dir, "guardrail_exit_allowed")
            return None

        dq_err = self._check_data_quality_guard(order, ticker)
        if dq_err:
            return dq_err

        access_token = self.auth.get_account_token()
        if self._trades_today() >= self._max_trades:
            _record_execution_metric(self.skill_dir, "guardrail_block_max_trades")
            return f"GUARDRAIL: Maximum daily trades ({self._max_trades}) exceeded. Blocking trade request."
        total, positions = self._get_account_balances(access_token)
        if total > self._max_total:
            _record_execution_metric(self.skill_dir, "guardrail_block_max_total")
            return f"GUARDRAIL: Total account value ${total:,.2f} exceeds maximum ${self._max_total:,.2f}. Blocking trade request."
        if order_value_usd is None:
            price = self._get_quote_price(ticker)
            if price is None or price <= 0:
                _record_execution_metric(self.skill_dir, "guardrail_block_price_unavailable")
                return f"GUARDRAIL: Could not resolve price for {ticker}. Blocking trade request."
            order_value_usd = abs(float(quantity)) * price
        else:
            order_value_usd = abs(float(order_value_usd))
        existing = positions.get(ticker.upper(), 0.0)
        new_val = existing + order_value_usd
        if new_val > self._max_pos:
            _record_execution_metric(self.skill_dir, "guardrail_block_max_position")
            return f"GUARDRAIL: Position size for {ticker} would be ${new_val:,.2f}, exceeding maximum ${self._max_pos:,.2f} per ticker. Blocking trade request."
        sec_err = self._check_sector_concentration(
            ticker, order_value_usd, total, positions
        )
        if sec_err:
            return sec_err
        return None

    def _check_sector_concentration(
        self,
        ticker: str,
        order_value_usd: float,
        total_equity: float,
        positions: dict[str, float],
    ) -> str | None:
        try:
            from config import get_max_sector_account_fraction

            max_frac = float(get_max_sector_account_fraction(self.skill_dir))
        except Exception:
            max_frac = 0.0
        if max_frac <= 0 or total_equity <= 0:
            return None
        try:
            from sector_strength import get_ticker_sector_etf
        except ImportError:
            return None
        target_etf = get_ticker_sector_etf(ticker, skill_dir=self.skill_dir)
        if not target_etf:
            return None
        sector_totals: dict[str, float] = {}
        for sym, mv in positions.items():
            etf = get_ticker_sector_etf(sym, skill_dir=self.skill_dir)
            key = etf or "UNKNOWN"
            sector_totals[key] = sector_totals.get(key, 0.0) + float(mv or 0)
        current = sector_totals.get(target_etf, 0.0)
        projected = current + float(order_value_usd)
        frac = projected / total_equity
        if frac > max_frac:
            _record_execution_metric(
                self.skill_dir,
                "guardrail_block_sector_concentration",
                reason=target_etf,
            )
            return (
                f"GUARDRAIL: Sector {target_etf} would be {frac:.1%} of account "
                f"(limit {max_frac:.1%}) after this order. Blocking new risk."
            )
        return None

    def _check_data_quality_guard(self, order: dict, ticker: str) -> str | None:
        """
        Degraded-mode policy at the guardrail boundary: optional block/warn for
        risk-increasing orders when market data quality is not ok.
        """
        try:
            from config import get_data_quality_exec_policy

            policy = get_data_quality_exec_policy(self.skill_dir)
        except Exception:
            policy = "off"
        if policy == "off":
            return None
        if not order or not self._increases_position(order):
            return None
        try:
            from data_health import assess_symbol_data_health

            snap = assess_symbol_data_health(ticker, self.auth, self.skill_dir)
        except Exception as e:
            if policy == "block_risk_increasing":
                _record_execution_metric(
                    self.skill_dir, "guardrail_block_data_health_error", reason=str(e)
                )
                return (
                    "GUARDRAIL: Data quality assessment failed "
                    f"({e}). Blocking risk-increasing order."
                )
            return None
        status = str(snap.get("data_quality") or "ok")
        if status == "ok":
            return None
        reasons = snap.get("reasons") or []
        rtxt = "; ".join(str(x) for x in reasons[:4])
        if policy == "warn":
            _record_execution_metric(self.skill_dir, "data_quality_warn", reason=f"{status}:{rtxt}")
            logging.getLogger(__name__).warning(
                "Data quality %s for %s: %s", status, ticker, rtxt
            )
            return None
        if policy == "block_risk_increasing":
            _record_execution_metric(self.skill_dir, "guardrail_block_data_quality", reason=status)
            msg = (
                f"GUARDRAIL: Data quality is '{status}' ({rtxt}). "
                "Blocking new risk-increasing order "
                "(DATA_QUALITY_EXEC_POLICY=block_risk_increasing)."
            )
            return msg
        return None


def _equity_order_payload(
    ticker: str,
    qty: int,
    side: str,
    order_type: str,
    limit_price: float | None = None,
) -> dict[str, Any]:
    leg = {
        "instruction": side,
        "quantity": qty,
        "instrument": {"symbol": ticker.upper(), "assetType": "EQUITY"},
    }
    payload: dict[str, Any] = {
        "orderStrategyType": "SINGLE",
        "session": "NORMAL",
        "duration": "DAY",
        "orderType": order_type,
        "orderLegCollection": [leg],
    }
    if order_type == "LIMIT" and limit_price is not None:
        payload["price"] = float(limit_price)
    return payload


def _trailing_stop_payload(
    ticker: str,
    qty: int,
    exec_price: float | None,
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    stop_pct = _compute_adaptive_stop_pct(ticker, exec_price, skill_dir=skill_dir)
    try:
        from config import get_stop_order_duration
        duration = get_stop_order_duration(Path(skill_dir or SKILL_DIR))
    except Exception:
        duration = "GOOD_TILL_CANCEL"
    if exec_price is not None and exec_price > 0:
        stop_type, offset = "VALUE", float(exec_price) * float(stop_pct)
    else:
        stop_type, offset = "PERCENT", float(stop_pct) * 100.0
    return {
        "orderStrategyType": "SINGLE",
        "session": "NORMAL",
        "duration": duration,
        "orderType": "TRAILING_STOP",
        "orderLegCollection": [{
            "instruction": "SELL",
            "quantity": qty,
            "instrument": {"symbol": ticker.upper(), "assetType": "EQUITY"},
        }],
        "stopPriceBasis": "LAST",
        "stopPriceType": stop_type,
        "stopPriceOffset": float(offset),
    }


def _hard_stop_payload(
    ticker: str,
    qty: int,
    stop_price: float,
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    try:
        from config import get_stop_order_duration

        duration = get_stop_order_duration(Path(skill_dir or SKILL_DIR))
    except Exception:
        duration = "GOOD_TILL_CANCEL"
    return {
        "orderStrategyType": "SINGLE",
        "session": "NORMAL",
        "duration": duration,
        "orderType": "STOP",
        "stopPrice": round(float(stop_price), 2),
        "orderLegCollection": [
            {
                "instruction": "SELL",
                "quantity": qty,
                "instrument": {"symbol": ticker.upper(), "assetType": "EQUITY"},
            }
        ],
    }


def _get_exit_manager_settings(skill_dir: Path) -> dict[str, Any]:
    mode = "off"
    partial_r = 1.5
    partial_fraction = 0.5
    breakeven_after_partial = True
    max_hold_days = 12
    try:
        from config import (
            get_exit_breakeven_after_partial,
            get_exit_manager_mode,
            get_exit_max_hold_days,
            get_exit_partial_tp_fraction,
            get_exit_partial_tp_r_mult,
        )

        mode = str(get_exit_manager_mode(skill_dir) or "off").strip().lower()
        partial_r = float(get_exit_partial_tp_r_mult(skill_dir))
        partial_fraction = float(get_exit_partial_tp_fraction(skill_dir))
        breakeven_after_partial = bool(get_exit_breakeven_after_partial(skill_dir))
        max_hold_days = int(get_exit_max_hold_days(skill_dir))
    except Exception:
        pass
    return {
        "mode": mode,
        "partial_r_mult": max(0.1, partial_r),
        "partial_fraction": max(0.05, min(0.95, partial_fraction)),
        "breakeven_after_partial": breakeven_after_partial,
        "max_hold_days": max(1, max_hold_days),
    }


def register_exit_manager_entry(
    *,
    skill_dir: Path | str | None,
    ticker: str,
    entry_order_id: str,
    qty: int,
    entry_price: float | None,
    stop_order_id: str | None = None,
    stop_pct: float | None = None,
) -> None:
    skill_dir_p = Path(skill_dir or SKILL_DIR)
    settings = _get_exit_manager_settings(skill_dir_p)
    if settings["mode"] == "off":
        return
    if entry_price is None or entry_price <= 0:
        return
    key = _exit_position_key(ticker, entry_order_id)
    with _EXIT_MANAGER_LOCK:
        state = _load_exit_manager_state(skill_dir_p)
        positions = state.setdefault("positions", {})
        links = state.setdefault("order_links", {})
        pending_meta = (state.setdefault("pending_entry_meta", {}) or {}).get(entry_order_id, {})
        staged_stop_order_id = pending_meta.get("stop_order_id")
        staged_stop_pct = _safe_float(pending_meta.get("stop_pct"))
        existing = positions.get(key) or {}
        if existing.get("status") == "closed":
            return
        now = datetime.utcnow().isoformat() + "Z"
        pos = {
            "position_key": key,
            "ticker": ticker.upper(),
            "entry_order_id": entry_order_id,
            "entry_qty": int(qty),
            "remaining_qty": int(existing.get("remaining_qty") or qty),
            "entry_price": float(entry_price),
            "entry_date": existing.get("entry_date") or date.today().isoformat(),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "status": existing.get("status") or "active",
            "stop_order_id": stop_order_id or staged_stop_order_id or existing.get("stop_order_id"),
            "stop_pct": (
                float(stop_pct)
                if stop_pct and stop_pct > 0
                else float(staged_stop_pct if staged_stop_pct and staged_stop_pct > 0 else (existing.get("stop_pct") or 0.07))
            ),
            "partial_tp_done": bool(existing.get("partial_tp_done")),
            "partial_tp_order_id": existing.get("partial_tp_order_id"),
            "pending_breakeven_move": bool(existing.get("pending_breakeven_move")),
            "breakeven_done": bool(existing.get("breakeven_done")),
            "breakeven_stop_order_id": existing.get("breakeven_stop_order_id"),
            "time_stop_done": bool(existing.get("time_stop_done")),
            "time_stop_order_id": existing.get("time_stop_order_id"),
            "shadow_partial_recorded": bool(existing.get("shadow_partial_recorded")),
            "shadow_breakeven_recorded": bool(existing.get("shadow_breakeven_recorded")),
            "shadow_time_stop_recorded": bool(existing.get("shadow_time_stop_recorded")),
        }
        positions[key] = pos
        links[entry_order_id] = {"position_key": key, "action": "entry"}
        if stop_order_id:
            links[stop_order_id] = {"position_key": key, "action": "initial_stop"}
        state.setdefault("pending_entry_meta", {}).pop(entry_order_id, None)
        _save_exit_manager_state(skill_dir_p, state)
    _record_execution_metric(skill_dir_p, "exit_manager_entry_registered")


def stage_exit_manager_entry_meta(
    *,
    skill_dir: Path | str | None,
    entry_order_id: str,
    ticker: str,
    stop_order_id: str | None,
    stop_pct: float | None,
) -> None:
    skill_dir_p = Path(skill_dir or SKILL_DIR)
    settings = _get_exit_manager_settings(skill_dir_p)
    if settings["mode"] == "off":
        return
    with _EXIT_MANAGER_LOCK:
        state = _load_exit_manager_state(skill_dir_p)
        pending = state.setdefault("pending_entry_meta", {})
        existing = pending.get(entry_order_id) or {}
        pending[entry_order_id] = {
            "ticker": ticker.upper(),
            "stop_order_id": stop_order_id or existing.get("stop_order_id"),
            "stop_pct": float(stop_pct) if stop_pct and stop_pct > 0 else existing.get("stop_pct"),
        }
        state["pending_entry_meta"] = pending
        _save_exit_manager_state(skill_dir_p, state)


def on_exit_manager_sell_fill(
    *,
    skill_dir: Path | str | None,
    ticker: str,
    order_id: str,
    qty: int,
) -> None:
    skill_dir_p = Path(skill_dir or SKILL_DIR)
    settings = _get_exit_manager_settings(skill_dir_p)
    if settings["mode"] == "off":
        return
    with _EXIT_MANAGER_LOCK:
        state = _load_exit_manager_state(skill_dir_p)
        positions = state.setdefault("positions", {})
        links = state.setdefault("order_links", {})
        link = links.get(order_id)
        position_key = link.get("position_key") if isinstance(link, dict) else None
        pos = positions.get(position_key) if position_key else None
        if not pos:
            for key, item in positions.items():
                if item.get("ticker") == ticker.upper() and item.get("status") == "active":
                    position_key = key
                    pos = item
                    break
        if not pos:
            return
        action = (link or {}).get("action") if isinstance(link, dict) else "sell_fill"
        remaining = max(0, int(pos.get("remaining_qty") or 0) - max(0, int(qty)))
        pos["remaining_qty"] = remaining
        pos["updated_at"] = datetime.utcnow().isoformat() + "Z"
        if action == "partial_tp":
            pos["partial_tp_done"] = True
            if settings["breakeven_after_partial"]:
                pos["pending_breakeven_move"] = True
        if action == "time_stop":
            pos["time_stop_done"] = True
        if remaining <= 0:
            pos["status"] = "closed"
            pos["closed_at"] = datetime.utcnow().isoformat() + "Z"
        positions[position_key] = pos
        _save_exit_manager_state(skill_dir_p, state)
    _record_execution_metric(skill_dir_p, "exit_manager_sell_fill_processed", reason=action or "sell_fill")


def run_exit_manager_sweep(
    *,
    auth: "DualSchwabAuth",
    skill_dir: Path | str | None,
    account_hash: str | None = None,
    ticker_filter: str | None = None,
) -> None:
    skill_dir_p = Path(skill_dir or SKILL_DIR)
    settings = _get_exit_manager_settings(skill_dir_p)
    mode = settings["mode"]
    if mode == "off":
        return

    with _EXIT_MANAGER_LOCK:
        state = _load_exit_manager_state(skill_dir_p)

    positions = state.get("positions", {}) or {}
    if not positions:
        return

    token = auth.get_account_token()
    resolved_hash = account_hash or _get_account_hash_for_orders(token, skill_dir_p, auth=auth)
    orders_url = f"{SCHWAB_BASE}/trader/v1/accounts/{resolved_hash}/orders" if resolved_hash else ""
    changed = False

    for key, pos in list(positions.items()):
        if not isinstance(pos, dict):
            continue
        if pos.get("status") != "active":
            continue
        ticker = str(pos.get("ticker") or "").upper()
        if not ticker:
            continue
        if ticker_filter and ticker != ticker_filter.upper():
            continue
        remaining_qty = max(0, int(pos.get("remaining_qty") or 0))
        if remaining_qty <= 0:
            pos["status"] = "closed"
            changed = True
            continue

        quote = _get_quote_quality_snapshot(ticker, auth, skill_dir_p)
        last = _safe_float(quote.get("last")) or _safe_float(quote.get("mid")) or _safe_float(quote.get("bid"))
        entry_px = _safe_float(pos.get("entry_price"))
        stop_pct = max(0.01, _safe_float(pos.get("stop_pct")) or 0.07)
        entry_date_raw = str(pos.get("entry_date") or date.today().isoformat())
        try:
            held_days = max(0, (date.today() - date.fromisoformat(entry_date_raw)).days)
        except Exception:
            held_days = 0

        partial_trigger = (
            entry_px * (1.0 + (settings["partial_r_mult"] * stop_pct))
            if entry_px and entry_px > 0
            else None
        )

        if not pos.get("partial_tp_done") and not pos.get("partial_tp_order_id") and partial_trigger and last and last >= partial_trigger:
            partial_qty = max(1, int(round(float(pos.get("entry_qty") or remaining_qty) * settings["partial_fraction"])))
            partial_qty = min(partial_qty, remaining_qty)
            if mode == "shadow":
                if not pos.get("shadow_partial_recorded"):
                    _record_execution_metric(skill_dir_p, "exit_manager_shadow_would_partial_tp")
                    pos["shadow_partial_recorded"] = True
                    changed = True
            else:
                if not orders_url:
                    _record_execution_metric(skill_dir_p, "exit_manager_live_error", reason="missing_account_hash")
                else:
                    payload = _equity_order_payload(ticker, partial_qty, "SELL", "MARKET")
                    try:
                        resp = _post_order_with_refresh(orders_url, payload, auth)
                        resp.raise_for_status()
                        order_loc = resp.headers.get("Location", "")
                        order_id = order_loc.split("/")[-1] if order_loc else None
                        if order_id:
                            pos["partial_tp_order_id"] = order_id
                            state.setdefault("order_links", {})[order_id] = {
                                "position_key": key,
                                "action": "partial_tp",
                            }
                            try:
                                from self_study import register_pending_order

                                register_pending_order(
                                    order_id,
                                    ticker,
                                    "SELL",
                                    partial_qty,
                                    last,
                                    skill_dir=skill_dir_p,
                                )
                            except Exception as e:
                                logging.getLogger(__name__).debug("Exit manager pending register failed: %s", e)
                            try:
                                from order_monitor import start_fill_monitor

                                start_fill_monitor(
                                    resolved_hash,
                                    order_id,
                                    auth.get_account_token(),
                                    ticker,
                                    "SELL",
                                    partial_qty,
                                    skill_dir_p,
                                    auth=auth,
                                )
                            except Exception as e:
                                logging.getLogger(__name__).debug("Exit manager monitor start failed: %s", e)
                            _record_execution_metric(skill_dir_p, "exit_manager_partial_tp_placed")
                            changed = True
                    except Exception as e:
                        _record_execution_metric(skill_dir_p, "exit_manager_live_error", reason=str(e))

        if pos.get("pending_breakeven_move") and not pos.get("breakeven_done") and remaining_qty > 0:
            if mode == "shadow":
                if not pos.get("shadow_breakeven_recorded"):
                    _record_execution_metric(skill_dir_p, "exit_manager_shadow_would_move_stop")
                    pos["shadow_breakeven_recorded"] = True
                    changed = True
            else:
                if not orders_url:
                    _record_execution_metric(skill_dir_p, "exit_manager_live_error", reason="missing_account_hash")
                else:
                    try:
                        existing_stop_id = pos.get("stop_order_id") or pos.get("breakeven_stop_order_id")
                        if existing_stop_id:
                            stop_url = f"{orders_url}/{existing_stop_id}"
                            cancel_resp = _cancel_order_with_refresh(stop_url, auth)
                            if not (cancel_resp.ok or cancel_resp.status_code in (200, 202, 204)):
                                _record_execution_metric(
                                    skill_dir_p,
                                    "exit_manager_live_error",
                                    reason=f"breakeven_cancel_failed:{cancel_resp.status_code}",
                                )
                        be_stop = _hard_stop_payload(
                            ticker,
                            remaining_qty,
                            stop_price=float(entry_px or 0.0),
                            skill_dir=skill_dir_p,
                        )
                        be_resp = _post_order_with_refresh(orders_url, be_stop, auth)
                        be_resp.raise_for_status()
                        be_loc = be_resp.headers.get("Location", "")
                        be_order_id = be_loc.split("/")[-1] if be_loc else None
                        pos["breakeven_done"] = True
                        pos["pending_breakeven_move"] = False
                        if be_order_id:
                            pos["breakeven_stop_order_id"] = be_order_id
                            pos["stop_order_id"] = be_order_id
                            state.setdefault("order_links", {})[be_order_id] = {
                                "position_key": key,
                                "action": "breakeven_stop",
                            }
                        _record_execution_metric(skill_dir_p, "exit_manager_breakeven_stop_moved")
                        changed = True
                    except Exception as e:
                        _record_execution_metric(skill_dir_p, "exit_manager_live_error", reason=str(e))

        if not pos.get("time_stop_done") and not pos.get("time_stop_order_id") and held_days >= int(settings["max_hold_days"]):
            if mode == "shadow":
                if not pos.get("shadow_time_stop_recorded"):
                    _record_execution_metric(skill_dir_p, "exit_manager_shadow_would_time_stop")
                    pos["shadow_time_stop_recorded"] = True
                    changed = True
            else:
                if not orders_url:
                    _record_execution_metric(skill_dir_p, "exit_manager_live_error", reason="missing_account_hash")
                else:
                    payload = _equity_order_payload(ticker, remaining_qty, "SELL", "MARKET")
                    try:
                        resp = _post_order_with_refresh(orders_url, payload, auth)
                        resp.raise_for_status()
                        order_loc = resp.headers.get("Location", "")
                        order_id = order_loc.split("/")[-1] if order_loc else None
                        pos["time_stop_done"] = True
                        if order_id:
                            pos["time_stop_order_id"] = order_id
                            state.setdefault("order_links", {})[order_id] = {
                                "position_key": key,
                                "action": "time_stop",
                            }
                            try:
                                from self_study import register_pending_order

                                register_pending_order(
                                    order_id,
                                    ticker,
                                    "SELL",
                                    remaining_qty,
                                    last,
                                    skill_dir=skill_dir_p,
                                )
                            except Exception as e:
                                logging.getLogger(__name__).debug("Exit manager time-stop pending register failed: %s", e)
                            try:
                                from order_monitor import start_fill_monitor

                                start_fill_monitor(
                                    resolved_hash,
                                    order_id,
                                    auth.get_account_token(),
                                    ticker,
                                    "SELL",
                                    remaining_qty,
                                    skill_dir_p,
                                    auth=auth,
                                )
                            except Exception as e:
                                logging.getLogger(__name__).debug("Exit manager time-stop monitor start failed: %s", e)
                        _record_execution_metric(skill_dir_p, "exit_manager_time_stop_placed")
                        changed = True
                    except Exception as e:
                        _record_execution_metric(skill_dir_p, "exit_manager_live_error", reason=str(e))

        pos["updated_at"] = datetime.utcnow().isoformat() + "Z"
        positions[key] = pos

    if changed:
        with _EXIT_MANAGER_LOCK:
            state["positions"] = positions
            _save_exit_manager_state(skill_dir_p, state)


def _compute_adaptive_stop_pct(
    ticker: str,
    exec_price: float | None,
    skill_dir: Path | str | None = None,
) -> float:
    skill_dir_p = Path(skill_dir or SKILL_DIR)
    try:
        from config import (
            get_adaptive_stop_atr_mult,
            get_adaptive_stop_base_pct,
            get_adaptive_stop_enabled,
            get_adaptive_stop_max_pct,
            get_adaptive_stop_min_pct,
            get_adaptive_stop_trend_lookback,
        )

        base_pct = float(get_adaptive_stop_base_pct(skill_dir_p))
        if not get_adaptive_stop_enabled(skill_dir_p):
            return max(0.01, base_pct)
        min_pct = float(get_adaptive_stop_min_pct(skill_dir_p))
        max_pct = float(get_adaptive_stop_max_pct(skill_dir_p))
        atr_mult = float(get_adaptive_stop_atr_mult(skill_dir_p))
        lookback = max(10, int(get_adaptive_stop_trend_lookback(skill_dir_p)))
    except Exception:
        return 0.07

    try:
        from market_data import get_daily_history
        from stage_analysis import add_indicators

        auth = DualSchwabAuth(skill_dir=skill_dir_p)
        df = get_daily_history(ticker, days=max(lookback + 35, 60), auth=auth, skill_dir=skill_dir_p)
        if df.empty or len(df) < 20:
            return max(min_pct, min(max_pct, base_pct))
        df = add_indicators(df)
        price = float(exec_price) if exec_price and exec_price > 0 else float(df["close"].iloc[-1])
        atr = float(df["atr_14"].iloc[-1]) if "atr_14" in df.columns else 0.0
        if price <= 0 or atr <= 0:
            return max(min_pct, min(max_pct, base_pct))
        atr_pct = atr / price
        trend_prev = float(df["close"].iloc[-(lookback + 1)]) if len(df) > lookback else float(df["close"].iloc[0])
        trend_lookback = ((price / trend_prev) - 1.0) if trend_prev > 0 else 0.0
        stop_pct = atr_pct * atr_mult
        if trend_lookback < -0.03:
            stop_pct *= 1.2
        elif trend_lookback > 0.06:
            stop_pct *= 0.9
        return max(min_pct, min(max_pct, stop_pct))
    except Exception:
        return max(min_pct, min(max_pct, base_pct))


def _parse_trader_error(resp: requests.Response) -> str:
    """Extract user-friendly message from Trader API error response."""
    try:
        data = resp.json()
        errors = data.get("errors", [])
        if errors:
            first = errors[0]
            detail = str(first.get("detail") or "").strip()
            title = str(first.get("title") or "").strip()
            blob = f"{title} {detail}".lower()
            if "client not authorized" in blob or "not authorized" in blob:
                return (
                    "Client not authorized for Schwab trading API. Reconnect Schwab in Setup and confirm "
                    "your app has Accounts and Trading Production access with your brokerage account linked."
                )
            if first.get("title") == "Internal Server Error":
                return (
                    "Trader API unavailable. In Schwab Developer Portal: "
                    "1) Ensure Account app has 'Accounts and Trading Production' "
                    "2) Link your brokerage account to the app "
                    "3) Contact traderapi@schwab.com if approved but still failing."
                )
            return detail or title or resp.text[:200]
    except Exception:
        pass
    return resp.text[:200] if resp.text else str(resp.status_code)


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _safe_telemetry_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(out)


def _safe_telemetry_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _build_standard_telemetry(
    *,
    mirofish_conviction: Any,
    advisory_prob: Any,
    agent_uncertainty: Any,
    vcp_volume_ratio: Any,
    sector_rs_rank: Any,
) -> dict[str, Any]:
    return {
        "mirofish_conviction": _safe_telemetry_float(mirofish_conviction),
        "advisory_prob": _safe_telemetry_float(advisory_prob),
        "agent_uncertainty": _safe_telemetry_float(agent_uncertainty),
        "vcp_volume_ratio": _safe_telemetry_float(vcp_volume_ratio),
        "sector_rs_rank": _safe_telemetry_int(sector_rs_rank),
    }


def _extract_quote_number(quote: dict[str, Any], keys: list[str]) -> float | None:
    layers: list[dict[str, Any]] = []
    if isinstance(quote, dict):
        layers.append(quote)
        nested = quote.get("quote")
        if isinstance(nested, dict):
            layers.append(nested)
    for layer in layers:
        for key in keys:
            if key in layer:
                num = _safe_float(layer.get(key))
                if num is not None and num > 0:
                    return num
    return None


def _get_quote_quality_snapshot(
    ticker: str,
    auth: "DualSchwabAuth",
    skill_dir: Path,
) -> dict[str, Any]:
    q = get_current_quote(ticker, auth=auth, skill_dir=skill_dir) or {}
    bid = _extract_quote_number(q, ["bidPrice", "bid", "bid_price"])
    ask = _extract_quote_number(q, ["askPrice", "ask", "ask_price"])
    last = _extract_quote_number(q, ["lastPrice", "mark", "closePrice", "regularMarketLastPrice"])
    mid = None
    spread_bps = None
    if bid is not None and ask is not None and ask >= bid and (ask + bid) > 0:
        mid = (ask + bid) / 2.0
        if mid > 0:
            spread_bps = ((ask - bid) / mid) * 10000.0
    return {
        "bid": bid,
        "ask": ask,
        "last": last,
        "mid": mid,
        "spread_bps": spread_bps,
    }


def _estimate_expected_slippage_bps(
    side: str,
    order_type: str,
    quote_snapshot: dict[str, Any],
    limit_price: float | None = None,
) -> float | None:
    side_n = side.strip().upper()
    order_type_n = order_type.strip().upper()
    ref = quote_snapshot.get("last") or quote_snapshot.get("mid")
    if ref is None or ref <= 0:
        return None

    touch = None
    if order_type_n == "MARKET":
        if side_n == "BUY":
            touch = quote_snapshot.get("ask") or ref
            return max(0.0, ((float(touch) - float(ref)) / float(ref)) * 10000.0)
        touch = quote_snapshot.get("bid") or ref
        return max(0.0, ((float(ref) - float(touch)) / float(ref)) * 10000.0)

    if order_type_n == "LIMIT" and limit_price is not None:
        lp = float(limit_price)
        if side_n == "BUY":
            return max(0.0, ((lp - float(ref)) / float(ref)) * 10000.0)
        return max(0.0, ((float(ref) - lp) / float(ref)) * 10000.0)
    return None


def _post_order_with_refresh(url: str, payload: dict[str, Any], auth: "DualSchwabAuth") -> requests.Response:
    token = auth.get_account_token()
    try:
        resp = requests.post(url, headers=_get_headers(token), json=payload, timeout=30)
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        raise
    if resp.status_code == 401 and auth.account_session.force_refresh():
        token = auth.get_account_token()
        try:
            resp = requests.post(url, headers=_get_headers(token), json=payload, timeout=30)
        except Exception as e:
            maybe_trip_breaker(e, schwab_circuit)
            raise
    return resp


def _get_order_with_refresh(order_url: str, auth: "DualSchwabAuth") -> requests.Response:
    token = auth.get_account_token()
    try:
        resp = requests.get(order_url, headers=_get_headers(token, for_get=True), timeout=30)
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        raise
    if resp.status_code == 401 and auth.account_session.force_refresh():
        token = auth.get_account_token()
        try:
            resp = requests.get(order_url, headers=_get_headers(token, for_get=True), timeout=30)
        except Exception as e:
            maybe_trip_breaker(e, schwab_circuit)
            raise
    return resp


def _replace_order_with_refresh(order_url: str, payload: dict[str, Any], auth: "DualSchwabAuth") -> requests.Response:
    token = auth.get_account_token()
    try:
        resp = requests.put(order_url, headers=_get_headers(token), json=payload, timeout=30)
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        raise
    if resp.status_code == 401 and auth.account_session.force_refresh():
        token = auth.get_account_token()
        try:
            resp = requests.put(order_url, headers=_get_headers(token), json=payload, timeout=30)
        except Exception as e:
            maybe_trip_breaker(e, schwab_circuit)
            raise
    return resp


def _cancel_order_with_refresh(order_url: str, auth: "DualSchwabAuth") -> requests.Response:
    token = auth.get_account_token()
    try:
        resp = requests.delete(order_url, headers=_get_headers(token, for_get=True), timeout=30)
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        raise
    if resp.status_code == 401 and auth.account_session.force_refresh():
        token = auth.get_account_token()
        try:
            resp = requests.delete(order_url, headers=_get_headers(token, for_get=True), timeout=30)
        except Exception as e:
            maybe_trip_breaker(e, schwab_circuit)
            raise
    return resp


def _run_limit_reprice_loop(
    *,
    orders_url: str,
    order_id: str | None,
    initial_payload: dict[str, Any],
    side: str,
    ticker: str,
    auth: "DualSchwabAuth",
    skill_dir: Path,
    attempts: int,
    interval_sec: int,
) -> tuple[str | None, dict[str, Any], list[dict[str, Any]]]:
    if not order_id or attempts <= 0:
        return order_id, initial_payload, []

    active_order_id = order_id
    active_payload = dict(initial_payload)
    history: list[dict[str, Any]] = []
    terminal_statuses = {"FILLED", "REJECTED", "CANCELED", "EXPIRED"}

    for idx in range(attempts):
        time.sleep(max(1, int(interval_sec)))
        order_url = f"{orders_url}/{active_order_id}"
        try:
            status_resp = _get_order_with_refresh(order_url, auth)
            status = ""
            if status_resp.ok:
                status_data = status_resp.json() if status_resp.text else {}
                status = str((status_data or {}).get("status") or "").upper()
            if status == "FILLED":
                _record_execution_metric(skill_dir, "exec_quality_reprice_order_filled")
                history.append({"attempt": idx + 1, "status": "filled", "order_id": active_order_id})
                break
            if status in terminal_statuses and status:
                history.append({"attempt": idx + 1, "status": status.lower(), "order_id": active_order_id})
                break

            snap = _get_quote_quality_snapshot(ticker, auth, skill_dir)
            new_limit = snap.get("ask") if side == "BUY" else snap.get("bid")
            old_limit = _safe_float(active_payload.get("price"))
            if new_limit is None or new_limit <= 0 or old_limit is None:
                _record_execution_metric(skill_dir, "exec_quality_reprice_skipped_no_quote")
                history.append({"attempt": idx + 1, "status": "skipped_no_quote", "order_id": active_order_id})
                continue
            new_limit = round(float(new_limit), 2)
            old_limit = round(float(old_limit), 2)
            if abs(new_limit - old_limit) < 0.01:
                history.append({"attempt": idx + 1, "status": "skipped_no_change", "order_id": active_order_id})
                continue

            new_payload = dict(active_payload)
            new_payload["price"] = new_limit

            replaced = False
            try:
                replace_resp = _replace_order_with_refresh(order_url, new_payload, auth)
                if replace_resp.ok:
                    replaced = True
                    active_payload = new_payload
                    _record_execution_metric(skill_dir, "exec_quality_reprice_replace")
                    history.append(
                        {
                            "attempt": idx + 1,
                            "status": "replaced",
                            "from_price": old_limit,
                            "to_price": new_limit,
                            "order_id": active_order_id,
                        }
                    )
            except Exception as replace_err:
                logging.getLogger(__name__).debug("Limit replace failed: %s", replace_err)

            if replaced:
                continue

            cancel_resp = _cancel_order_with_refresh(order_url, auth)
            if not (cancel_resp.ok or cancel_resp.status_code in (200, 202, 204)):
                _record_execution_metric(
                    skill_dir,
                    "exec_quality_reprice_cancel_failed",
                    reason=f"status={cancel_resp.status_code}",
                )
                history.append(
                    {"attempt": idx + 1, "status": "cancel_failed", "order_id": active_order_id}
                )
                continue

            repost_resp = _post_order_with_refresh(orders_url, new_payload, auth)
            repost_resp.raise_for_status()
            new_location = repost_resp.headers.get("Location", "")
            new_order_id = new_location.split("/")[-1] if new_location else active_order_id
            active_order_id = new_order_id
            active_payload = new_payload
            _record_execution_metric(skill_dir, "exec_quality_reprice_cancel_replace")
            history.append(
                {
                    "attempt": idx + 1,
                    "status": "cancel_replace",
                    "from_price": old_limit,
                    "to_price": new_limit,
                    "order_id": active_order_id,
                }
            )
        except Exception as e:
            _record_execution_metric(skill_dir, "exec_quality_reprice_error", reason=str(e))
            history.append({"attempt": idx + 1, "status": "error", "error": str(e), "order_id": active_order_id})
            break

    return active_order_id, active_payload, history


def _get_account_hash_for_orders(access_token: str, skill_dir: Path, auth: "DualSchwabAuth | None" = None) -> str | None:
    """
    Fetch account hash from /accounts/accountNumbers - REQUIRED for order placement.
    GET /accounts may return accountNumber (masked) but orders need hashValue from this endpoint.
    """
    # Env override
    env_path = skill_dir / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("SCHWAB_ACCOUNT_HASH="):
                val = line.split("=", 1)[1].strip().strip('"\'')
                if val:
                    return val
    url = f"{SCHWAB_BASE}/trader/v1/accounts/accountNumbers"
    if not schwab_circuit.connection_stable:
        return None
    try:
        resp = requests.get(url, headers=_get_headers(access_token, for_get=True), timeout=30)
        if resp.status_code == 401 and auth and auth.account_session.force_refresh():
            access_token = auth.get_account_token()
            resp = requests.get(url, headers=_get_headers(access_token, for_get=True), timeout=30)
        if not resp.ok:
            return None
        data = resp.json()
        # Response: array of {accountNumber, hashValue} or single object
        if isinstance(data, list) and data:
            first = data[0]
            return first.get("hashValue") or first.get("hash_value")
        if isinstance(data, dict):
            return data.get("hashValue") or data.get("hash_value")
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        pass
    return None


def get_account_status(
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> dict | str:
    """Fetch account status using Account Session."""
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    try:
        if not schwab_circuit.connection_stable:
            return "Error: Schwab connection unstable (circuit breaker)"
        token = auth.get_account_token()
        url = f"{SCHWAB_BASE}/trader/v1/accounts"
        resp = requests.get(
            url,
            headers=_get_headers(token, for_get=True),
            params={"fields": "positions"},
            timeout=30,
        )
        if resp.status_code == 401 and auth.account_session.force_refresh():
            token = auth.get_account_token()
            resp = requests.get(
                url,
                headers=_get_headers(token, for_get=True),
                params={"fields": "positions"},
                timeout=30,
            )
        if not resp.ok and resp.status_code in (400, 404):
            # Some account configurations reject optional fields; retry base endpoint.
            resp = requests.get(url, headers=_get_headers(token, for_get=True), timeout=30)
        if not resp.ok:
            return _parse_trader_error(resp)
        data = resp.json()
        if isinstance(data, list):
            accounts = data
        elif isinstance(data, dict) and isinstance(data.get("accounts"), list):
            accounts = data.get("accounts", [])
        elif isinstance(data, dict) and isinstance(data.get("securitiesAccount"), dict):
            accounts = [data]
        else:
            accounts = []
        ids = []
        for acc in accounts:
            sec = acc.get("securitiesAccount", acc)
            aid = sec.get("hashValue") or sec.get("accountNumber")
            if aid:
                ids.append(str(aid))
        return {"accounts": accounts, "account_ids": ids}
    except requests.RequestException as e:
        maybe_trip_breaker(e, schwab_circuit)
        return f"Error: {e}"


def place_order(
    ticker: str,
    qty: int,
    side: str,
    order_type: str,
    limit_price: float | None = None,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
    price_hint: float | None = None,
    mirofish_conviction: int | float | None = None,
    advisory_prob: int | float | None = None,
    agent_uncertainty: int | float | None = None,
    vcp_volume_ratio: int | float | None = None,
    sector_rs_rank: int | None = None,
    sector_etf: str | None = None,
) -> str | dict:
    """
    Place equity order using ONLY Account Session. Passes through Guardrail Wrapper.
    On block: returns error string and sends warning via notifier.
    On BUY: attaches ATR-based trailing stop (2.5x ATR). On fill: sends success alert.
    """
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    wrapper = GuardrailWrapper(auth, skill_dir)
    skill_dir = Path(skill_dir or SKILL_DIR)

    side_n = side.strip().upper()
    order_type_n = order_type.strip().upper()
    ticker_n = ticker.upper().strip()
    telemetry = _build_standard_telemetry(
        mirofish_conviction=mirofish_conviction,
        advisory_prob=advisory_prob,
        agent_uncertainty=agent_uncertainty,
        vcp_volume_ratio=vcp_volume_ratio,
        sector_rs_rank=sector_rs_rank,
    )

    data_quality_payload: dict[str, Any] | None = None
    try:
        from data_health import assess_symbol_data_health, merge_operator_payload

        data_quality_payload = merge_operator_payload(
            assess_symbol_data_health(ticker_n, auth, skill_dir)
        )
    except Exception:
        data_quality_payload = None

    if side_n not in ("BUY", "SELL"):
        return "Error: side must be BUY or SELL"
    if order_type_n not in ("MARKET", "LIMIT"):
        return "Error: order_type must be MARKET or LIMIT"
    if order_type_n == "LIMIT" and limit_price is None:
        return "Error: limit_price required for LIMIT orders"

    try:
        token = auth.get_account_token()
        # Orders require hashValue from /accounts/accountNumbers, not from GET /accounts
        account_hash = _get_account_hash_for_orders(token, skill_dir, auth=auth)
        if not account_hash:
            # Fallback: try hashValue/accountNumber from GET /accounts (may cause 400 for orders)
            accts = wrapper._get_accounts(token)
            for acc in accts:
                sec = acc.get("securitiesAccount", acc)
                aid = sec.get("hashValue") or acc.get("hashValue")
                if aid:
                    account_hash = str(aid)
                    break
            if not account_hash:
                return "Error: No Schwab accounts available. Add SCHWAB_ACCOUNT_HASH to .env if needed."
    except Exception as e:
        return f"Error: {e}"

    try:
        run_exit_manager_sweep(auth=auth, skill_dir=skill_dir, account_hash=account_hash)
    except Exception as e:
        logging.getLogger(__name__).debug("Exit manager sweep skipped: %s", e)

    primary = _equity_order_payload(ticker_n, int(qty), side_n, order_type_n, limit_price)
    order_value = None
    if order_type_n == "LIMIT" and limit_price is not None:
        order_value = abs(float(qty)) * abs(float(limit_price))
    elif order_type_n == "MARKET":
        px = wrapper._get_quote_price(ticker_n)
        if px is None and price_hint is not None and price_hint > 0:
            px = float(price_hint)
        if px is not None:
            order_value = abs(float(qty)) * px

    try:
        from config import (
            get_live_trading_kill_switch,
            get_live_trading_kill_switch_blocks_exits,
            get_user_trading_halted,
        )

        halted = get_live_trading_kill_switch(skill_dir) or get_user_trading_halted(
            skill_dir
        )
        blocks_exits = get_live_trading_kill_switch_blocks_exits(skill_dir)
    except Exception:
        halted = False
        blocks_exits = False
    if halted and (blocks_exits or wrapper._increases_position(primary)):
        _record_execution_metric(skill_dir, "guardrail_block_trading_halted")
        msg = (
            "GUARDRAIL: Live trading is halted (platform kill switch or account pause). "
            "No order was sent."
        )
        send_alert(msg, kind="guardrail", env_path=skill_dir / ".env")
        return msg

    err = wrapper._check_guardrails(ticker_n, int(qty), primary, order_value)
    if err:
        _record_execution_metric(skill_dir, "guardrail_blocked_order")
        send_alert(f"Guardrail block: {err}", kind="guardrail", env_path=skill_dir / ".env")
        return err

    exec_quality_mode = "off"
    exec_spread_max_bps = 35
    exec_slippage_max_bps = 20
    exec_reprice_attempts = 2
    exec_reprice_interval_sec = 3
    exec_use_limit_for_liquid = True
    event_risk_mode = "off"
    event_action = "block"
    event_downsize_factor = 0.5
    regime_v2_mode = "off"
    regime_v2_entry_min_score = 55
    regime_v2_size_mult = {"high": 1.0, "medium": 0.7, "low": 0.4}
    try:
        from config import (
            get_event_action,
            get_event_downsize_factor,
            get_event_risk_mode,
            get_exec_quality_mode,
            get_exec_reprice_attempts,
            get_exec_reprice_interval_sec,
            get_exec_slippage_max_bps,
            get_exec_spread_max_bps,
            get_exec_use_limit_for_liquid,
            get_execution_shadow_mode,
            get_regime_v2_entry_min_score,
            get_regime_v2_mode,
            get_regime_v2_size_mult_high,
            get_regime_v2_size_mult_low,
            get_regime_v2_size_mult_med,
        )

        shadow_mode = get_execution_shadow_mode(skill_dir)
        exec_quality_mode = str(get_exec_quality_mode(skill_dir) or "off").strip().lower()
        exec_spread_max_bps = int(get_exec_spread_max_bps(skill_dir))
        exec_slippage_max_bps = int(get_exec_slippage_max_bps(skill_dir))
        exec_reprice_attempts = int(get_exec_reprice_attempts(skill_dir))
        exec_reprice_interval_sec = int(get_exec_reprice_interval_sec(skill_dir))
        exec_use_limit_for_liquid = bool(get_exec_use_limit_for_liquid(skill_dir))
        event_risk_mode = str(get_event_risk_mode(skill_dir) or "off").strip().lower()
        event_action = str(get_event_action(skill_dir) or "block").strip().lower()
        event_downsize_factor = float(get_event_downsize_factor(skill_dir))
        regime_v2_mode = str(get_regime_v2_mode(skill_dir) or "off").strip().lower()
        regime_v2_entry_min_score = int(get_regime_v2_entry_min_score(skill_dir))
        regime_v2_size_mult = {
            "high": float(get_regime_v2_size_mult_high(skill_dir)),
            "medium": float(get_regime_v2_size_mult_med(skill_dir)),
            "low": float(get_regime_v2_size_mult_low(skill_dir)),
        }
    except Exception:
        shadow_mode = False

    # Regime gate: block BUY orders when SPY < 200 SMA
    if not shadow_mode and side_n == "BUY":
        try:
            from sector_strength import is_market_regime_bullish
            regime_ok, regime_ctx = is_market_regime_bullish(auth, skill_dir)
            if not regime_ok:
                _record_execution_metric(skill_dir, "regime_blocked_order")
                msg = (
                    f"REGIME GATE: SPY ${regime_ctx.get('spy_price', '?')} below 200 SMA "
                    f"${regime_ctx.get('spy_sma_200', '?')}. No new BUY orders in bear regime."
                )
                send_alert(msg, kind="regime_block", env_path=skill_dir / ".env")
                return msg
        except Exception as e:
            logging.getLogger(__name__).warning("Regime check failed in execution: %s", e)

    # Sector filter: only trade in winning sectors (unless disabled)
    if not shadow_mode and _env_bool("SECTOR_FILTER_ENABLED", True):
        try:
            from sector_strength import get_winning_sector_etfs, is_ticker_in_winning_sector
            winning = get_winning_sector_etfs(auth, skill_dir)
            ok, msg = is_ticker_in_winning_sector(ticker_n, winning)
            if not ok:
                _record_execution_metric(skill_dir, "sector_blocked_order", reason=msg)
                send_alert(f"Sector block: {msg}", kind="sector_block", env_path=skill_dir / ".env")
                return msg
        except Exception as e:
            logging.getLogger(__name__).warning("Sector filter failed: %s", e)
            _record_execution_metric(skill_dir, "sector_filter_error", reason=str(e))
            send_alert(f"Sector filter error: {e}. Blocking trade for safety.", kind="sector_block", env_path=skill_dir / ".env")
            return f"Sector filter error: {e}"

    if not shadow_mode and not schwab_circuit.connection_stable:
        return "Error: Schwab connection unstable (circuit breaker)"

    exec_quality_diag: dict[str, Any] = {"mode": exec_quality_mode}
    if exec_quality_mode in {"shadow", "live"}:
        _record_execution_metric(skill_dir, "exec_quality_evaluated")
        snap = _get_quote_quality_snapshot(ticker_n, auth, skill_dir)
        spread_bps = _safe_float(snap.get("spread_bps"))
        expected_slippage_bps = _estimate_expected_slippage_bps(
            side_n,
            order_type_n,
            snap,
            limit_price=limit_price,
        )
        block_reasons: list[str] = []
        if spread_bps is not None and spread_bps > float(exec_spread_max_bps):
            block_reasons.append(f"spread_bps {spread_bps:.1f} > max {exec_spread_max_bps}")
        if expected_slippage_bps is not None and expected_slippage_bps > float(exec_slippage_max_bps):
            block_reasons.append(
                f"expected_slippage_bps {expected_slippage_bps:.1f} > max {exec_slippage_max_bps}"
            )

        liquid = (
            snap.get("bid") is not None
            and snap.get("ask") is not None
            and spread_bps is not None
            and spread_bps <= float(exec_spread_max_bps)
        )
        preferred_limit_price = snap.get("ask") if side_n == "BUY" else snap.get("bid")
        should_prefer_limit = bool(
            exec_use_limit_for_liquid
            and order_type_n == "MARKET"
            and liquid
            and preferred_limit_price is not None
            and float(preferred_limit_price) > 0
        )
        exec_quality_diag.update(
            {
                "spread_bps": round(float(spread_bps), 2) if spread_bps is not None else None,
                "expected_slippage_bps": (
                    round(float(expected_slippage_bps), 2)
                    if expected_slippage_bps is not None
                    else None
                ),
                "spread_max_bps": int(exec_spread_max_bps),
                "slippage_max_bps": int(exec_slippage_max_bps),
                "would_block": bool(block_reasons),
                "block_reasons": block_reasons,
                "would_prefer_limit": bool(should_prefer_limit),
                "quote_snapshot": {
                    "bid": snap.get("bid"),
                    "ask": snap.get("ask"),
                    "last": snap.get("last"),
                },
            }
        )

        if exec_quality_mode == "shadow":
            if block_reasons:
                _record_execution_metric(
                    skill_dir,
                    "exec_quality_shadow_would_block",
                    reason="; ".join(block_reasons),
                )
            if should_prefer_limit:
                _record_execution_metric(skill_dir, "exec_quality_shadow_would_prefer_limit")
        elif exec_quality_mode == "live":
            if block_reasons:
                reason_txt = "; ".join(block_reasons)
                _record_execution_metric(skill_dir, "exec_quality_live_blocked", reason=reason_txt)
                msg = f"EXECUTION QUALITY BLOCK: {reason_txt}"
                send_alert(msg, kind="guardrail", env_path=skill_dir / ".env")
                return msg
            if should_prefer_limit and preferred_limit_price is not None:
                limit_px = round(float(preferred_limit_price), 2)
                primary = _equity_order_payload(ticker_n, int(qty), side_n, "LIMIT", limit_px)
                order_type_n = "LIMIT"
                limit_price = limit_px
                order_value = abs(float(qty)) * abs(float(limit_px))
                exec_quality_diag.update(
                    {
                        "limit_upgrade_applied": True,
                        "limit_upgrade_price": limit_px,
                    }
                )
                _record_execution_metric(skill_dir, "exec_quality_live_limit_upgrade")
                # Re-check guardrails using final adapted order payload/value.
                guardrail_recheck = wrapper._check_guardrails(ticker_n, int(qty), primary, order_value)
                if guardrail_recheck:
                    _record_execution_metric(skill_dir, "guardrail_blocked_order")
                    send_alert(
                        f"Guardrail block: {guardrail_recheck}",
                        kind="guardrail",
                        env_path=skill_dir / ".env",
                    )
                    return guardrail_recheck

    event_risk_diag: dict[str, Any] = {"mode": event_risk_mode, "action": event_action}
    if side_n == "BUY" and event_risk_mode in {"shadow", "live"}:
        try:
            from signal_scanner import evaluate_event_risk_policy

            policy = evaluate_event_risk_policy(ticker_n, skill_dir=skill_dir)
            flagged = bool(policy.get("flagged"))
            event_risk_diag.update(policy)
            if flagged:
                _record_execution_metric(skill_dir, "event_risk_flagged", reason=";".join(policy.get("reasons", [])))
                if event_risk_mode == "shadow":
                    if event_action == "downsize":
                        _record_execution_metric(skill_dir, "event_risk_shadow_would_downsize")
                    else:
                        _record_execution_metric(skill_dir, "event_risk_shadow_would_block")
                elif event_risk_mode == "live":
                    if event_action == "block":
                        _record_execution_metric(skill_dir, "event_risk_blocked", reason=";".join(policy.get("reasons", [])))
                        msg = (
                            "EVENT RISK BLOCK: "
                            + ", ".join(policy.get("reasons", [])[:2])
                            + f" for {ticker_n}. Try after event window."
                        )
                        send_alert(msg, kind="guardrail", env_path=skill_dir / ".env")
                        return msg
                    if event_action == "downsize":
                        before_qty = int(qty)
                        factor = max(0.10, min(1.0, float(event_downsize_factor)))
                        downsized_qty = max(1, int(round(before_qty * factor)))
                        if downsized_qty < before_qty:
                            qty = downsized_qty
                            primary = _equity_order_payload(
                                ticker_n,
                                int(qty),
                                side_n,
                                order_type_n,
                                limit_price,
                            )
                            if order_type_n == "LIMIT" and limit_price is not None:
                                order_value = abs(float(qty)) * abs(float(limit_price))
                            elif order_type_n == "MARKET":
                                px = wrapper._get_quote_price(ticker_n)
                                if px is None and price_hint is not None and price_hint > 0:
                                    px = float(price_hint)
                                order_value = abs(float(qty)) * px if px is not None else None
                            event_risk_diag["downsized_qty_before"] = before_qty
                            event_risk_diag["downsized_qty_after"] = int(qty)
                            _record_execution_metric(
                                skill_dir,
                                "event_risk_downsized",
                                reason=f"{before_qty}->{int(qty)}",
                            )
        except Exception as e:
            _record_execution_metric(skill_dir, "event_risk_eval_error", reason=str(e))

    regime_v2_diag: dict[str, Any] = {"mode": regime_v2_mode}
    if side_n == "BUY" and regime_v2_mode in {"shadow", "live"}:
        try:
            from sector_strength import get_regime_v2_snapshot

            snap = get_regime_v2_snapshot(auth, skill_dir)
            score = float(snap.get("score", 0) or 0)
            bucket = str(snap.get("bucket") or "low").lower()
            mult = float(regime_v2_size_mult.get(bucket, regime_v2_size_mult.get("low", 0.4)))
            regime_v2_diag.update(
                {
                    "score": round(score, 2),
                    "bucket": bucket,
                    "entry_min_score": int(regime_v2_entry_min_score),
                    "size_multiplier": mult,
                    "components": snap.get("components"),
                }
            )
            _record_execution_metric(skill_dir, "regime_v2_evaluated")
            if regime_v2_mode == "shadow":
                regime_v2_diag["shadow_action"] = "would_gate_or_resize"
            else:
                if score < float(regime_v2_entry_min_score):
                    _record_execution_metric(
                        skill_dir,
                        "regime_v2_blocked",
                        reason=f"score {score:.1f} < min {regime_v2_entry_min_score}",
                    )
                    msg = (
                        f"REGIME V2 BLOCK: composite score {score:.1f} below threshold "
                        f"{regime_v2_entry_min_score}. Entry skipped."
                    )
                    send_alert(msg, kind="regime_block", env_path=skill_dir / ".env")
                    return msg
                before_qty = int(qty)
                after_qty = max(1, int(round(float(before_qty) * max(0.10, min(1.5, mult)))))
                if after_qty != before_qty:
                    qty = after_qty
                    primary = _equity_order_payload(ticker_n, int(qty), side_n, order_type_n, limit_price)
                    if order_type_n == "LIMIT" and limit_price is not None:
                        order_value = abs(float(qty)) * abs(float(limit_price))
                    elif order_type_n == "MARKET":
                        px = wrapper._get_quote_price(ticker_n)
                        if px is None and price_hint is not None and price_hint > 0:
                            px = float(price_hint)
                        order_value = abs(float(qty)) * px if px is not None else None
                    regime_v2_diag["qty_before"] = before_qty
                    regime_v2_diag["qty_after"] = int(qty)
                    _record_execution_metric(
                        skill_dir,
                        "regime_v2_sized",
                        reason=f"bucket={bucket}:{before_qty}->{int(qty)}",
                    )
        except Exception as e:
            _record_execution_metric(skill_dir, "regime_v2_eval_error", reason=str(e))

    url = f"{SCHWAB_BASE}/trader/v1/accounts/{account_hash}/orders"

    if shadow_mode:
        _record_execution_metric(skill_dir, "action_shadow")
        shadow_result: dict[str, Any] = {
            "shadow_mode": True,
            "shadow_action": "would_place_order",
            "ticker": ticker_n,
            "qty": int(qty),
            "side": side_n,
            "order_type": order_type_n,
            "estimated_order_value_usd": round(float(order_value), 2) if order_value is not None else None,
            "guardrails_passed": True,
            "sector_filter_passed": True,
            "order_payload_preview": primary,
            "execution_quality": exec_quality_diag,
            "event_risk": event_risk_diag,
            "regime_v2": regime_v2_diag,
            "telemetry": telemetry,
            "data_quality": (data_quality_payload or {}).get("data_quality"),
            "data_quality_reasons": (data_quality_payload or {}).get("data_quality_reasons"),
        }
        if side_n == "BUY":
            exec_price = wrapper._get_quote_price(ticker_n)
            shadow_result["_stop_protection"] = {
                "enabled": True,
                "status": "shadow_simulated",
                "duration": _trailing_stop_payload(
                    ticker_n, int(qty), exec_price, skill_dir=skill_dir
                ).get("duration"),
            }
        else:
            shadow_result["_stop_protection"] = {"enabled": False, "status": "not_applicable"}
        send_alert(
            f"[SHADOW MODE] Would place {side_n} {qty} {ticker_n} ({order_type_n}). No live order was submitted.",
            kind="order_filled",
            env_path=skill_dir / ".env",
        )
        return shadow_result

    try:
        resp = _post_order_with_refresh(url, primary, auth)
        resp.raise_for_status()
        _record_execution_metric(skill_dir, "action_live")
        order_location = resp.headers.get("Location", "")
        result = resp.json() if resp.text else {}
        order_id = order_location.split("/")[-1] if order_location else None
        if exec_quality_mode == "live" and primary.get("orderType") == "LIMIT":
            order_id, primary, reprice_history = _run_limit_reprice_loop(
                orders_url=url,
                order_id=order_id,
                initial_payload=primary,
                side=side_n,
                ticker=ticker_n,
                auth=auth,
                skill_dir=skill_dir,
                attempts=max(0, int(exec_reprice_attempts)),
                interval_sec=max(1, int(exec_reprice_interval_sec)),
            )
            if reprice_history:
                result["_execution_quality_reprice"] = reprice_history
                exec_quality_diag["reprice_attempts"] = len(reprice_history)
        wrapper._record_trade(ticker_n, order_id or order_location)
        if order_id and "orderId" not in result:
            result["orderId"] = order_id
        if exec_quality_mode in {"shadow", "live"}:
            result["_execution_quality"] = exec_quality_diag
        if event_risk_mode in {"shadow", "live"}:
            result["_event_risk"] = event_risk_diag
        if regime_v2_mode in {"shadow", "live"}:
            result["_regime_v2"] = regime_v2_diag
        if data_quality_payload:
            result["_data_quality"] = data_quality_payload
        result["telemetry"] = telemetry

        # Self-study: record trade outcome for learning
        if order_id:
            try:
                from self_study import register_pending_order
                register_pending_order(
                    order_id, ticker_n, side_n, int(qty), price_hint,
                    skill_dir=skill_dir,
                    mirofish_conviction=mirofish_conviction,
                    sector_etf=sector_etf,
                )
            except Exception as e:
                logging.getLogger(__name__).debug("Self-study register failed: %s", e)

        # Start fill monitor (notify on FILLED/REJECTED)
        if order_id:
            try:
                from order_monitor import start_fill_monitor
                start_fill_monitor(
                    account_hash, order_id, auth.get_account_token(),
                    ticker_n,
                    side_n,
                    int(qty),
                    skill_dir,
                    auth=auth,
                    exit_context={"entry_order_id": order_id} if side_n == "BUY" else None,
                )
            except Exception as e:
                logging.getLogger(__name__).debug("Fill monitor not started: %s", e)

        if side_n == "BUY":
            exec_price = wrapper._get_quote_price(ticker_n)
            exit_stop_pct = _compute_adaptive_stop_pct(ticker_n, exec_price, skill_dir=skill_dir)
            stop_order = _trailing_stop_payload(ticker_n, int(qty), exec_price, skill_dir=skill_dir)
            stop_resp = None
            hard_resp = None
            for attempt in range(3):  # Retry up to 3 times
                token = auth.get_account_token()
                try:
                    stop_resp = requests.post(url, headers=_get_headers(token), json=stop_order, timeout=30)
                except Exception as e:
                    maybe_trip_breaker(e, schwab_circuit)
                    raise
                if stop_resp.status_code == 401 and auth.account_session.force_refresh():
                    token = auth.get_account_token()
                    try:
                        stop_resp = requests.post(url, headers=_get_headers(token), json=stop_order, timeout=30)
                    except Exception as e:
                        maybe_trip_breaker(e, schwab_circuit)
                        raise
                if stop_resp.ok:
                    break
                if attempt < 2:
                    import time
                    time.sleep(2)
            stop_ok = stop_resp is not None and stop_resp.ok
            used_fallback_stop = False
            if not stop_ok:
                try:
                    base_price = exec_price if exec_price and exec_price > 0 else wrapper._get_quote_price(ticker_n)
                    stop_pct = _compute_adaptive_stop_pct(ticker_n, base_price, skill_dir=skill_dir)
                    if base_price and base_price > 0:
                        hard_stop = _hard_stop_payload(
                            ticker_n,
                            int(qty),
                            stop_price=float(base_price) * (1.0 - float(stop_pct)),
                            skill_dir=skill_dir,
                        )
                        token = auth.get_account_token()
                        hard_resp = requests.post(url, headers=_get_headers(token), json=hard_stop, timeout=30)
                        if hard_resp.status_code == 401 and auth.account_session.force_refresh():
                            token = auth.get_account_token()
                            hard_resp = requests.post(url, headers=_get_headers(token), json=hard_stop, timeout=30)
                        if hard_resp.ok:
                            used_fallback_stop = True
                            stop_ok = True
                            _record_execution_metric(skill_dir, "stop_protection_fallback_attached")
                except Exception as fallback_e:
                    logging.getLogger(__name__).debug("Hard-stop fallback failed: %s", fallback_e)
            if stop_ok:
                active_stop_resp = hard_resp if used_fallback_stop and hard_resp is not None else stop_resp
                stop_location = active_stop_resp.headers.get("Location", "") if active_stop_resp is not None else ""
                stop_order_id = stop_location.split("/")[-1] if stop_location else None
                if order_id:
                    try:
                        stage_exit_manager_entry_meta(
                            skill_dir=skill_dir,
                            entry_order_id=order_id,
                            ticker=ticker_n,
                            stop_order_id=stop_order_id,
                            stop_pct=exit_stop_pct,
                        )
                    except Exception as e:
                        logging.getLogger(__name__).debug("Exit manager meta stage failed: %s", e)
                if stop_order_id:
                    try:
                        from self_study import register_pending_order
                        register_pending_order(
                            stop_order_id, ticker_n, "SELL", int(qty),
                            exec_price if exec_price else None,
                            skill_dir=skill_dir,
                        )
                    except Exception as e:
                        logging.getLogger(__name__).debug("Self-study trailing stop register failed: %s", e)
                    try:
                        from order_monitor import start_fill_monitor
                        start_fill_monitor(
                            account_hash, stop_order_id, auth.get_account_token(),
                            ticker_n, "SELL", int(qty), skill_dir, auth=auth,
                        )
                    except Exception as e:
                        logging.getLogger(__name__).debug("Stop fill monitor not started: %s", e)
            if not stop_ok:
                err_text = stop_resp.text[:200] if stop_resp else "No response"
                err_code = getattr(stop_resp, "status_code", "?") if stop_resp else "?"
                if order_id:
                    try:
                        stage_exit_manager_entry_meta(
                            skill_dir=skill_dir,
                            entry_order_id=order_id,
                            ticker=ticker_n,
                            stop_order_id=None,
                            stop_pct=exit_stop_pct,
                        )
                    except Exception:
                        pass
                _record_execution_metric(skill_dir, "stop_protection_failed", reason=f"{err_code}: {err_text}")
                send_alert(
                    f"BUY placed but trailing stop FAILED: {err_code} {err_text}. "
                    f"Manually add stop for {qty} {ticker_n}!",
                    kind="trailing_stop_failed",
                    env_path=skill_dir / ".env",
                )
                result["_stop_placed"] = False
                result["_stop_protection"] = {
                    "enabled": True,
                    "status": "failed",
                    "error_code": str(err_code),
                    "error_text": err_text,
                }
            else:
                _record_execution_metric(skill_dir, "stop_protection_attached")
                result["_stop_placed"] = True
                result["_stop_protection"] = {
                    "enabled": True,
                    "status": "attached_fallback" if used_fallback_stop else "attached",
                    "duration": stop_order.get("duration"),
                }
            send_alert(
                f"Order placed: {side_n} {qty} {ticker_n}. Trailing stop {'attached' if stop_ok else 'FAILED—add manually'}.",
                kind="order_filled" if stop_ok else "trailing_stop_failed",
                env_path=skill_dir / ".env",
            )
        else:
            result["_stop_protection"] = {"enabled": False, "status": "not_applicable"}
        return result
    except requests.HTTPError as e:
        msg = f"API Error: {e.response.status_code} - {e.response.text[:300]}"
        send_alert(msg, kind="order_rejected", env_path=skill_dir / ".env")
        return msg
    except Exception as e:
        msg = f"Error: {e}"
        send_alert(msg, kind="order_rejected", env_path=skill_dir / ".env")
        return msg
