"""
Per-tenant skill directory for SaaS workers.

Materializes a temporary directory with .env and Schwab token files so existing
modules (DualSchwabAuth, signal_scanner, execution) work unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.orm import Session

from schwab_auth import write_encrypted_token_file

from .models import User, UserCredential
from .security import decrypt_secret

# Optional platform overrides forwarded into each tenant .env when set in process env.
_ENV_OPTIONAL_FOR_TENANT = (
    "PAPER_TRADING_ENABLED",
    "EXECUTION_SHADOW_MODE",
    "MAX_SECTOR_ACCOUNT_FRACTION",
    "HYPOTHESIS_LEDGER_ENABLED",
    "HYPOTHESIS_SELF_STUDY_MERGE",
)

# Env keys written into tenant .env (platform must supply Schwab app registration).
_ENV_KEYS_FOR_TENANT = (
    "SCHWAB_MARKET_APP_KEY",
    "SCHWAB_MARKET_APP_SECRET",
    "SCHWAB_MARKET_CALLBACK_URL",
    "SCHWAB_ACCOUNT_APP_KEY",
    "SCHWAB_ACCOUNT_APP_SECRET",
    "SCHWAB_CALLBACK_URL",
    "SCHWAB_TOKEN_ENCRYPTION_KEY",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_USER_ID",
)


def _decrypt_json_payload(enc: str | None) -> dict[str, Any] | None:
    if not enc:
        return None
    raw = decrypt_secret(enc)
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def _account_token_dict(row: UserCredential) -> dict[str, Any] | None:
    blob = _decrypt_json_payload(row.account_token_payload_enc)
    if blob and blob.get("access_token") and blob.get("refresh_token"):
        return blob
    access = decrypt_secret(row.access_token_enc)
    refresh = decrypt_secret(row.refresh_token_enc)
    if access and refresh:
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": (row.token_type or "Bearer").strip() or "Bearer",
        }
    return None


def _market_token_dict(row: UserCredential) -> dict[str, Any] | None:
    blob = _decrypt_json_payload(row.market_token_payload_enc)
    if blob and blob.get("access_token") and blob.get("refresh_token"):
        return blob
    return None


def user_has_account_session(db: Session, user_id: str) -> bool:
    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not row:
        return False
    return _account_token_dict(row) is not None


def user_schwab_ready_for_live_trading(db: Session, user_id: str) -> tuple[bool, str]:
    """Account + market data path available (same bar as running a scan / placing guarded orders)."""
    if not user_has_account_session(db, user_id):
        return False, "Schwab account tokens are not linked."
    ok, reason = user_can_materialize_for_scan(db, user_id)
    if not ok:
        return False, reason
    return True, ""


def user_can_materialize_for_scan(db: Session, user_id: str) -> tuple[bool, str]:
    if not user_has_account_session(db, user_id):
        return False, "Schwab account tokens are not linked."
    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    assert row is not None
    if _market_token_dict(row):
        return True, ""
    platform_dir = (os.getenv("SAAS_PLATFORM_MARKET_SKILL_DIR") or "").strip()
    if platform_dir and (Path(platform_dir) / "tokens_market.enc").is_file():
        return True, ""
    return (
        False,
        "Market session missing: provide market_oauth_json on credentials or set "
        "SAAS_PLATFORM_MARKET_SKILL_DIR to a skill dir containing tokens_market.enc.",
    )


def _write_tenant_env(skill_dir: Path) -> None:
    lines: list[str] = []
    for key in _ENV_KEYS_FOR_TENANT:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            lines.append(f"{key}={val}")
    for key in _ENV_OPTIONAL_FOR_TENANT:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            lines.append(f"{key}={val}")
    if not any(line.startswith("SCHWAB_CALLBACK_URL=") for line in lines):
        lines.append("SCHWAB_CALLBACK_URL=https://127.0.0.1:8182")
    (skill_dir / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_tenant_execution_overrides(skill_dir: Path, db: Session, user_id: str) -> None:
    extra: list[str] = []
    plat = (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower()
    if plat in ("1", "true", "yes", "on"):
        extra.append("LIVE_TRADING_KILL_SWITCH=1")
    be = (os.getenv("LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS") or "").strip().lower()
    if be in ("1", "true", "yes", "on"):
        extra.append("LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS=1")
    row = db.query(User).filter(User.id == user_id).first()
    if row and getattr(row, "trading_halted", False):
        extra.append("USER_TRADING_HALTED=1")
    if not extra:
        return
    path = skill_dir / ".env"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(existing.rstrip() + "\n" + "\n".join(extra) + "\n", encoding="utf-8")


def materialize_tenant_skill_dir(db: Session, user_id: str, skill_dir: Path) -> None:
    """Populate skill_dir with .env and token files. Raises RuntimeError on misconfiguration."""
    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not row:
        raise RuntimeError("No credentials row for user.")

    market_secret = (os.environ.get("SCHWAB_MARKET_APP_SECRET") or "").strip()
    account_secret = (os.environ.get("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    market_key = (os.environ.get("SCHWAB_MARKET_APP_KEY") or "").strip()
    account_key = (os.environ.get("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    if not all([market_secret, account_secret, market_key, account_key]):
        raise RuntimeError(
            "Platform Schwab app env missing: set SCHWAB_MARKET_APP_KEY/SECRET and "
            "SCHWAB_ACCOUNT_APP_KEY/SECRET on the API and worker processes."
        )

    skill_dir.mkdir(parents=True, exist_ok=True)
    _write_tenant_env(skill_dir)
    _append_tenant_execution_overrides(skill_dir, db, user_id)

    account = _account_token_dict(row)
    if not account:
        raise RuntimeError("Schwab account OAuth tokens are missing or incomplete.")
    write_encrypted_token_file(skill_dir / "tokens_account.enc", account, account_secret)

    market = _market_token_dict(row)
    platform_dir = (os.getenv("SAAS_PLATFORM_MARKET_SKILL_DIR") or "").strip()
    if market:
        write_encrypted_token_file(skill_dir / "tokens_market.enc", market, market_secret)
    elif platform_dir:
        src = Path(platform_dir) / "tokens_market.enc"
        if not src.is_file():
            raise RuntimeError(
                f"SAAS_PLATFORM_MARKET_SKILL_DIR set but tokens_market.enc missing: {src}"
            )
        shutil.copy(src, skill_dir / "tokens_market.enc")
    else:
        raise RuntimeError(
            "Market OAuth not configured: upload market_oauth_json or set SAAS_PLATFORM_MARKET_SKILL_DIR."
        )


@contextmanager
def tenant_skill_dir(db: Session, user_id: str) -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=f"tb_saas_{user_id[:24]}_"))
    try:
        materialize_tenant_skill_dir(db, user_id, root)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
