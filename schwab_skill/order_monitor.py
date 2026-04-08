"""
Order fill monitor: poll order status and notify on FILLED or REJECTED.
Runs in background thread. Sends Discord alert when terminal state reached.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests

LOG = logging.getLogger(__name__)
SCHWAB_BASE = "https://api.schwabapi.com"
POLL_INTERVAL = 10  # seconds
MAX_POLLS = 60  # ~10 min max wait
TERMINAL_STATUSES = frozenset({"FILLED", "REJECTED", "CANCELED", "EXPIRED"})


def _get_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _extract_fill_price(data: dict) -> float | None:
    """Extract fill/average price from Schwab order response."""
    def _to_float(v: Any) -> float | None:
        try:
            x = float(v)
            return x if x > 0 else None
        except (TypeError, ValueError):
            return None

    # Try common response fields
    for key in ("averagePrice", "averageFillPrice", "price", "filledPrice"):
        px = _to_float(data.get(key))
        if px is not None:
            return px

    # Parse execution legs and compute weighted average if quantities are available.
    acts = data.get("orderActivityCollection")
    if isinstance(acts, list):
        weighted_total = 0.0
        total_qty = 0.0
        fallback_prices: list[float] = []
        for act in acts:
            if not isinstance(act, dict):
                continue
            ex_legs = act.get("executionLegs")
            if not isinstance(ex_legs, list):
                continue
            for ex in ex_legs:
                if not isinstance(ex, dict):
                    continue
                px = _to_float(ex.get("price") or ex.get("averagePrice"))
                if px is None:
                    continue
                qty = _to_float(ex.get("quantity"))
                if qty is None:
                    qty = _to_float(ex.get("quantityFilled"))
                if qty is not None:
                    weighted_total += px * qty
                    total_qty += qty
                fallback_prices.append(px)
        if total_qty > 0:
            return weighted_total / total_qty
        if fallback_prices:
            return sum(fallback_prices) / len(fallback_prices)

    # Secondary fallback: flattened leg-level price fields.
    legs = data.get("orderLegCollection")
    if isinstance(legs, list):
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            px = _to_float(leg.get("price") or leg.get("averagePrice"))
            if px is not None:
                return px
    return None


def _poll_order_status(
    account_hash: str,
    order_id: str,
    access_token: str,
    ticker: str,
    side: str,
    qty: int,
    env_path: Path,
    auth: Any = None,
    skill_dir: Path | None = None,
    exit_context: dict[str, Any] | None = None,
) -> None:
    """Poll order status until FILLED/REJECTED or timeout. Send Discord on terminal state.
    If auth is provided and a 401 occurs, refresh token and retry."""
    from notifier import send_alert

    url = f"{SCHWAB_BASE}/trader/v1/accounts/{account_hash}/orders/{order_id}"
    token = access_token
    for _ in range(MAX_POLLS):
        try:
            resp = requests.get(url, headers=_get_headers(token), timeout=10)
            if resp.status_code == 401 and auth is not None:
                try:
                    if getattr(auth.account_session, "force_refresh", lambda: False)():
                        token = auth.get_account_token()
                        resp = requests.get(url, headers=_get_headers(token), timeout=10)
                except Exception as e:
                    LOG.debug("Token refresh during poll failed: %s", e)
            if not resp.ok:
                time.sleep(POLL_INTERVAL)
                continue
            data = resp.json() or {}
            status = (data.get("status") or "").upper()
            if status in TERMINAL_STATUSES:
                if status == "FILLED":
                    fill_price = _extract_fill_price(data)
                    if side == "BUY" and skill_dir:
                        try:
                            from hold_reminder import add_position
                            add_position(ticker, int(qty), skill_dir=skill_dir)
                        except Exception as e:
                            LOG.debug("Hold tracker update on fill failed: %s", e)
                    if skill_dir:
                        try:
                            from execution import (
                                on_exit_manager_sell_fill,
                                register_exit_manager_entry,
                                run_exit_manager_sweep,
                            )

                            if side == "BUY":
                                register_exit_manager_entry(
                                    skill_dir=skill_dir,
                                    ticker=ticker,
                                    entry_order_id=order_id,
                                    qty=int(qty),
                                    entry_price=float(fill_price) if fill_price is not None else None,
                                    stop_order_id=(exit_context or {}).get("stop_order_id"),
                                    stop_pct=(exit_context or {}).get("stop_pct"),
                                )
                            else:
                                on_exit_manager_sell_fill(
                                    skill_dir=skill_dir,
                                    ticker=ticker,
                                    order_id=order_id,
                                    qty=int(qty),
                                )
                            if auth is not None:
                                run_exit_manager_sweep(
                                    auth=auth,
                                    skill_dir=skill_dir,
                                    account_hash=account_hash,
                                    ticker_filter=ticker,
                                )
                        except Exception as e:
                            LOG.debug("Exit manager update on fill failed: %s", e)
                    if fill_price is not None and skill_dir:
                        try:
                            from self_study import upsert_filled_order

                            upsert_filled_order(
                                order_id=order_id,
                                ticker=ticker,
                                side=side,
                                qty=int(qty),
                                fill_price=float(fill_price),
                                skill_dir=skill_dir,
                            )
                            if side == "SELL":
                                from self_study import run_self_study

                                run_self_study(skill_dir=skill_dir)
                        except Exception as e:
                            LOG.debug("Self-study fill update failed: %s", e)
                    fill_msg = (
                        f"Order FILLED: {side} {qty} {ticker}"
                        + (f" @ ${fill_price:,.2f}" if fill_price else "")
                        + (" (trailing stop triggered – position sold)" if side == "SELL" else "")
                    )
                    send_alert(fill_msg, kind="order_filled", env_path=env_path)
                    LOG.info(fill_msg)
                elif status == "REJECTED":
                    reason = data.get("rejectedReason") or data.get("message") or "Unknown"
                    err_msg = f"Order REJECTED: {side} {qty} {ticker}. Reason: {reason}"
                    send_alert(err_msg, kind="order_rejected", env_path=env_path)
                    LOG.warning(err_msg)
                return
        except Exception as e:
            LOG.warning("Order status poll error: %s", e)
        time.sleep(POLL_INTERVAL)
    send_alert(
        f"Order status timeout: {side} {qty} {ticker} (order_id={order_id}). Check Schwab manually.",
        kind="order_timeout",
        env_path=env_path,
    )


def start_fill_monitor(
    account_hash: str,
    order_id: str,
    access_token: str,
    ticker: str,
    side: str,
    qty: int,
    skill_dir: Path,
    auth: Any = None,
    exit_context: dict[str, Any] | None = None,
) -> None:
    """Start background thread to monitor order fill.
    Pass auth (DualSchwabAuth) to enable token refresh on 401 during long polls."""
    env_path = skill_dir / ".env"
    t = threading.Thread(
        target=_poll_order_status,
        args=(account_hash, order_id, access_token, ticker, side, qty, env_path, auth, skill_dir, exit_context),
        daemon=True,
    )
    t.start()
