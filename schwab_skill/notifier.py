"""
Discord notification module for the trading bot.

Reads DISCORD_WEBHOOK_URL and DISCORD_USER_ID from .env.
Supports varied alert kinds with distinct styling. Error-level alerts
include a user mention to ping the phone. Retries on failure.
"""

import logging
import os
from pathlib import Path
from typing import Any

import requests

from circuit_breaker import discord_circuit, maybe_trip_breaker

# Load .env into os.environ if not already set
_ENV_LOADED = False
_LOG = logging.getLogger(__name__)
_RETRIES = 3
_RETRY_DELAY = 2.0
_TITLE_MAX = 256
_DESC_MAX = 4096
_FIELD_NAME_MAX = 256
_FIELD_VALUE_MAX = 1024
_FIELDS_MAX = 25
_FOOTER_MAX = 2048

# Alert kinds: (emoji, title, color)
# Colors: blue=0x3498DB, green=0x00FF00, red=0xE74C3C, amber=0xF39C12, purple=0x9B59B6
ALERT_KINDS = {
    "heartbeat": ("💓", "Daily Heartbeat", 0x3498DB),
    "signal": ("📈", "New Signal", 0x2ECC71),
    "order_filled": ("✅", "Order Filled", 0x00FF00),
    "order_rejected": ("❌", "Order Rejected", 0xE74C3C),
    "order_timeout": ("⏱️", "Order Timeout", 0xE74C3C),
    "guardrail": ("🛡️", "Guardrail Block", 0xF39C12),
    "sector_block": ("📊", "Sector Block", 0xF39C12),
    "hold_reminder": ("📅", "Hold Reminder", 0xF1C40F),
    "data_failure": ("⚠️", "Data Failure", 0xE74C3C),
    "crash": ("🚨", "Bot Crash", 0xE74C3C),
    "scan_complete": ("🔍", "Scan Complete", 0x3498DB),
    "scan_data_issues": ("⚠️", "Scan Data Issues", 0xF39C12),
    "self_study": ("📚", "Self-Study", 0x9B59B6),
    "trailing_stop_failed": ("🛑", "Trailing Stop Failed", 0xE74C3C),
    "sector_filter_fallback": ("📊", "Sector Filter Fallback", 0xF39C12),
    "info": ("ℹ️", "Info", 0x3498DB),
    "success": ("✅", "Success", 0x00FF00),
    "error": ("❌", "Error", 0xE74C3C),
}


def _clip(value: object, limit: int, suffix: str = "...") -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)] + suffix


def _sanitize_embed(embed: dict) -> dict:
    out = dict(embed)
    if "title" in out:
        out["title"] = _clip(out.get("title", ""), _TITLE_MAX)
    if "description" in out:
        out["description"] = _clip(out.get("description", ""), _DESC_MAX)
    if isinstance(out.get("fields"), list):
        safe = []
        for f in out["fields"][:_FIELDS_MAX]:
            safe.append(
                {
                    "name": _clip(f.get("name", "Field"), _FIELD_NAME_MAX) or "Field",
                    "value": _clip(f.get("value", "—"), _FIELD_VALUE_MAX) or "—",
                    "inline": bool(f.get("inline", False)),
                }
            )
        out["fields"] = safe
    footer = out.get("footer")
    if isinstance(footer, dict) and "text" in footer:
        out["footer"] = {"text": _clip(footer.get("text", ""), _FOOTER_MAX)}
    return out


def _load_env(env_path: Path | str | None = None) -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    path = Path(env_path or Path(__file__).resolve().parent / ".env")
    if path.exists():
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    key = k.strip()
                    val = v.strip().strip('"\'')
                    if key not in os.environ:
                        os.environ[key] = val
    _ENV_LOADED = True


def send_alert(
    message: str,
    level: str = "info",
    kind: str | None = None,
    env_path: Path | str | None = None,
    operator_context: dict[str, Any] | None = None,
) -> bool:
    """
    Send an alert to Discord via webhook.

    level: 'info' | 'success' | 'error' (fallback when kind not set)
    kind: One of ALERT_KINDS keys for distinct styling (heartbeat, signal,
          order_filled, guardrail, hold_reminder, etc.)

    Error-level or critical kinds (order_rejected, data_failure, crash,
    trailing_stop_failed) include <@DISCORD_USER_ID> to ping the user.

    operator_context: optional dict for operator-facing metadata. When it contains
    data_quality / data_quality_reasons (or a nested merge_operator_payload), a field
    is added to the Discord embed.

    Returns True if sent successfully, False otherwise.
    """
    _load_env(env_path)

    if not discord_circuit.connection_stable:
        # Skip noisy retries when Discord connectivity is clearly unstable.
        return False

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    user_id = os.environ.get("DISCORD_USER_ID", "").strip()

    if not webhook_url:
        _LOG.debug("Discord webhook URL not set, skipping alert")
        return False

    # Resolve kind from level if not provided
    if kind is None:
        kind = level if level in ALERT_KINDS else ("error" if level == "error" else "info")

    _emoji, title, color = ALERT_KINDS.get(kind, ALERT_KINDS["info"])

    # Kinds that should ping the user
    ping_kinds = frozenset({
        "error", "order_rejected", "order_timeout", "data_failure",
        "crash", "trailing_stop_failed", "sector_block",
    })

    if kind in ping_kinds and user_id:
        prefix = f"<@{user_id}> "
    else:
        prefix = ""

    embed: dict[str, Any] = {
        "title": title,
        "description": f"{prefix}{message}".strip() or "(no message)",
        "color": color,
        "footer": {"text": f"Schwab Trading Bot • {kind.replace('_', ' ').title()}"},
    }
    ctx = operator_context or {}
    dq = ctx.get("data_quality")
    dqr = ctx.get("data_quality_reasons")
    if dq is None and isinstance(ctx.get("data_quality_payload"), dict):
        pl = ctx["data_quality_payload"]
        dq = pl.get("data_quality")
        dqr = pl.get("data_quality_reasons")
    if dq is not None:
        reason_txt = ""
        if isinstance(dqr, list) and dqr:
            reason_txt = "; ".join(str(x) for x in dqr[:4])
        embed.setdefault("fields", []).append({
            "name": "Data quality",
            "value": f"**{dq}**" + (f"\n{reason_txt}" if reason_txt else ""),
            "inline": False,
        })

    payload = {"embeds": [_sanitize_embed(embed)]}

    last_err = None
    for attempt in range(_RETRIES):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            maybe_trip_breaker(e, discord_circuit)
            last_err = e
            if attempt < _RETRIES - 1:
                import time
                time.sleep(_RETRY_DELAY)
    _LOG.warning("Discord alert failed after %d attempts: %s", _RETRIES, last_err)
    return False


def send_embed_alert(
    embed: dict,
    env_path: Path | str | None = None,
) -> bool:
    """
    Send a pre-built embed dict to Discord via webhook.
    Accepts a full embed dict with fields, footer, timestamp, etc.
    Returns True on success.
    """
    _load_env(env_path)

    if not discord_circuit.connection_stable:
        return False

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        _LOG.debug("Discord webhook URL not set, skipping embed alert")
        return False

    payload = {"embeds": [_sanitize_embed(embed)]}

    last_err = None
    for attempt in range(_RETRIES):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            maybe_trip_breaker(e, discord_circuit)
            last_err = e
            if attempt < _RETRIES - 1:
                import time
                time.sleep(_RETRY_DELAY)
    _LOG.warning("Discord embed alert failed after %d attempts: %s", _RETRIES, last_err)
    return False
