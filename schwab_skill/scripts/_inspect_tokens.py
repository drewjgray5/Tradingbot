"""One-off diagnostic: inspect Schwab token files.

Decrypts tokens_market.enc and tokens_account.enc using the client secrets
from .env, prints token freshness, scope, and refresh-token presence so we
can see whether OAuth re-auth actually wrote a new token.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from schwab_auth import _decrypt, _get_encryption_key  # noqa: E402


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _short(s: str) -> str:
    if not s:
        return "<empty>"
    if len(s) <= 12:
        return s
    return f"{s[:8]}...{s[-4:]} (len={len(s)})"


def main() -> int:
    env = _load_env(SKILL_DIR / ".env")
    targets = [
        ("tokens_market.enc", "SCHWAB_MARKET_APP_SECRET"),
        ("tokens_account.enc", "SCHWAB_ACCOUNT_APP_SECRET"),
    ]
    for fname, secret_key in targets:
        p = SKILL_DIR / fname
        print(f"=== {fname} ===")
        if not p.exists():
            print("  MISSING")
            continue
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        age = dt.datetime.now() - dt.datetime.fromtimestamp(p.stat().st_mtime)
        secret = env.get(secret_key, "")
        if not secret:
            print(f"  mtime={mtime} (age={age}) -- {secret_key} missing in .env")
            continue
        try:
            key = _get_encryption_key(secret)
            tok = _decrypt(p.read_bytes(), key)
        except Exception as e:
            print(f"  mtime={mtime} (age={age}) DECRYPT EXCEPTION: {type(e).__name__}: {e}")
            continue
        if not tok:
            print(
                f"  mtime={mtime} (age={age}) DECRYPT FAILED -- "
                f"likely the client_secret in .env doesn't match the secret used "
                f"when this token file was written."
            )
            continue
        access = str(tok.get("access_token", "") or "")
        refresh = str(tok.get("refresh_token", "") or "")
        print(f"  mtime={mtime} (age={age})")
        print(f"  access_token : {_short(access)}")
        print(f"  refresh_token: {_short(refresh)}")
        print(f"  scope        : {tok.get('scope', '')!r}")
        print(f"  token_type   : {tok.get('token_type', '')!r}")
        print(f"  expires_in   : {tok.get('expires_in', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
