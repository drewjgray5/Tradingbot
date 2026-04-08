"""
20-day hold period reminders: track positions bought through the bot and alert when held 20+ days.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
TRACKER_FILE = SKILL_DIR / ".positions_tracker.json"


def _get_hold_days(skill_dir: Path | None = None) -> int:
    """Hold period in days before reminder. Default 20. Override via HOLD_DAYS in .env."""
    skill_dir = skill_dir or SKILL_DIR
    env_path = skill_dir / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("HOLD_DAYS="):
                val = line.split("=", 1)[1].strip().strip('"\'')
                try:
                    return max(1, int(float(val)))
                except (ValueError, TypeError):
                    pass
    return 20


def _load_tracker(skill_dir: Path | None = None) -> list[dict]:
    path = (skill_dir or SKILL_DIR) / ".positions_tracker.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        LOG.warning("Failed to load positions tracker: %s", e)
        return []


def _save_tracker(entries: list[dict], skill_dir: Path | None = None) -> None:
    path = (skill_dir or SKILL_DIR) / ".positions_tracker.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2))
    except Exception as e:
        LOG.warning("Failed to save positions tracker: %s", e)


def add_position(ticker: str, qty: int, skill_dir: Path | None = None) -> None:
    """Record a new BUY position. Call when order fills (or when placed for market orders)."""
    ticker = ticker.upper().strip()
    skill_dir = skill_dir or SKILL_DIR
    entries = _load_tracker(skill_dir)
    today = date.today().isoformat()
    for e in entries:
        if e.get("ticker") == ticker and e.get("entry_date") == today:
            e["qty"] = e.get("qty", 0) + qty
            _save_tracker(entries, skill_dir)
            return
    entries.append({
        "ticker": ticker,
        "entry_date": today,
        "qty": qty,
        "hold_alert_sent": False,
    })
    _save_tracker(entries, skill_dir)


def _get_schwab_position_symbols(skill_dir: Path) -> set[str]:
    """Return set of ticker symbols currently held in Schwab account."""
    try:
        from execution import GuardrailWrapper
        from schwab_auth import DualSchwabAuth

        auth = DualSchwabAuth(skill_dir=skill_dir)
        wrapper = GuardrailWrapper(auth, skill_dir)
        _, positions = wrapper._get_account_balances(auth.get_account_token())
        return {s.upper() for s in positions if s}
    except Exception as e:
        LOG.warning("Could not fetch Schwab positions: %s", e)
        return set()


def check_hold_period_and_alert(skill_dir: Path | None = None) -> int:
    """
    Check tracked positions: remove sold ones, alert for those held 20+ days.
    Returns count of alerts sent.
    """
    skill_dir = skill_dir or SKILL_DIR
    env_path = skill_dir / ".env"
    today = date.today()
    alerts_sent = 0

    entries = _load_tracker(skill_dir)
    if not entries:
        return 0

    symbols_held = _get_schwab_position_symbols(skill_dir)
    updated: list[dict] = []

    for e in entries:
        ticker = (e.get("ticker") or "").upper()
        if not ticker:
            continue
        if ticker not in symbols_held:
            continue
        entry_str = e.get("entry_date", "")
        try:
            entry_date = date.fromisoformat(entry_str)
        except (ValueError, TypeError):
            updated.append(e)
            continue

        days_held = (today - entry_date).days
        hold_days = _get_hold_days(skill_dir)
        if days_held >= hold_days and not e.get("hold_alert_sent"):
            try:
                from notifier import send_alert
                msg = (
                    f"**Hold reminder:** {ticker} has been held {days_held} days "
                    f"(strategy suggests consider selling after {hold_days}). "
                    f"Trailing stop may still be active—check your Schwab account."
                )
                if send_alert(msg, kind="hold_reminder", env_path=env_path):
                    e["hold_alert_sent"] = True
                    alerts_sent += 1
            except Exception as ex:
                LOG.warning("Hold reminder alert failed for %s: %s", ticker, ex)

        updated.append(e)

    _save_tracker(updated, skill_dir)
    return alerts_sent
