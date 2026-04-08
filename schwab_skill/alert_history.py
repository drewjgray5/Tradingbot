"""
Track signal alerts per ticker to distinguish first-time vs repeat alerts.
Uses a JSON file to persist last-alert dates. Repeat = alerted within COOLDOWN_DAYS.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
HISTORY_FILE = SKILL_DIR / ".signal_alert_history.json"
COOLDOWN_DAYS = 5


def _load_history(skill_dir: Path | None = None) -> dict[str, str]:
    """Load {ticker: "YYYY-MM-DD"} from disk."""
    path = (skill_dir or SKILL_DIR) / ".signal_alert_history.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {k: v for k, v in (data.items() or {}) if isinstance(v, str)}
    except Exception:
        return {}


def _save_history(data: dict[str, str], skill_dir: Path | None = None) -> None:
    path = (skill_dir or SKILL_DIR) / ".signal_alert_history.json"
    try:
        path.write_text(json.dumps(data, indent=0))
    except Exception:
        pass


def get_alert_label(ticker: str, skill_dir: Path | None = None) -> str:
    """Return '🆕 First time' or '🔁 Repeat' based on whether we've alerted recently."""
    ticker = ticker.upper().strip()
    history = _load_history(skill_dir)
    last_str = history.get(ticker)
    if not last_str:
        return "🆕 First time"
    try:
        last = date.fromisoformat(last_str)
    except (ValueError, TypeError):
        return "🆕 First time"
    days_since = (date.today() - last).days
    if days_since > COOLDOWN_DAYS:
        return "🆕 First time"
    return "🔁 Repeat"


def record_alert_sent(ticker: str, skill_dir: Path | None = None) -> None:
    """Record that we sent an alert for this ticker today."""
    ticker = ticker.upper().strip()
    history = _load_history(skill_dir)
    history[ticker] = date.today().isoformat()
    _save_history(history, skill_dir)
