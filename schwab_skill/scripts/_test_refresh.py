"""One-off diagnostic: directly call Schwab refresh-token endpoint.

This tells us whether each session's refresh_token is still valid against
the app key/secret currently in .env. A 400/401 from this endpoint means
the refresh token has been invalidated (revoked, app secret rotated, or
re-auth produced a token bound to a different client_id).
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from schwab_auth import _decrypt, _get_encryption_key  # noqa: E402

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


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


def main() -> int:
    env = _load_env(SKILL_DIR / ".env")
    targets = [
        ("market", "tokens_market.enc", "SCHWAB_MARKET_APP_KEY", "SCHWAB_MARKET_APP_SECRET"),
        ("account", "tokens_account.enc", "SCHWAB_ACCOUNT_APP_KEY", "SCHWAB_ACCOUNT_APP_SECRET"),
    ]
    for name, fname, key_env, secret_env in targets:
        print(f"=== {name} ===")
        p = SKILL_DIR / fname
        if not p.exists():
            print(f"  {fname} MISSING -- nothing to refresh")
            continue
        client_id = env.get(key_env, "")
        client_secret = env.get(secret_env, "")
        if not client_id or not client_secret:
            print(f"  Missing {key_env} or {secret_env} in .env")
            continue
        try:
            key = _get_encryption_key(client_secret)
            tok = _decrypt(p.read_bytes(), key)
        except Exception as e:
            print(f"  decrypt failed: {type(e).__name__}: {e}")
            continue
        if not tok or not tok.get("refresh_token"):
            print("  no refresh_token in file")
            continue
        refresh = tok["refresh_token"]
        try:
            resp = requests.post(
                TOKEN_URL,
                auth=(client_id, client_secret),
                data={"grant_type": "refresh_token", "refresh_token": refresh},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except Exception as e:
            print(f"  HTTP error: {type(e).__name__}: {e}")
            continue
        body = (resp.text or "").strip().replace("\n", " ")[:300]
        print(f"  status={resp.status_code}")
        print(f"  body  ={body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
