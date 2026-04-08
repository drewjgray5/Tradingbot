"""
Dual-session OAuth2 authentication for Schwab Developer API.

Maintains TWO separate authenticated sessions:
- Market Session: SCHWAB_MARKET_APP_KEY / SCHWAB_MARKET_APP_SECRET (OHLCV, quotes)
- Account Session: SCHWAB_ACCOUNT_APP_KEY / SCHWAB_ACCOUNT_APP_SECRET (orders, balances)

Tokens saved securely in local JSON files. Background refresh before expiry.
"""

import base64
import json
import os
import threading
import urllib.parse
from pathlib import Path

import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from circuit_breaker import maybe_trip_breaker, schwab_circuit

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
REFRESH_INTERVAL_SEC = 25 * 60  # 25 minutes
KEY_ENV_VAR = "SCHWAB_TOKEN_ENCRYPTION_KEY"


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def _get_encryption_key(secret: str) -> bytes:
    key_b64 = os.environ.get(KEY_ENV_VAR)
    if key_b64:
        return key_b64.encode() if isinstance(key_b64, str) else key_b64
    return _derive_key(secret, b"schwab_dual_auth_v1")


def _encrypt(data: dict, key: bytes) -> bytes:
    return Fernet(key).encrypt(json.dumps(data).encode())


def _decrypt(encrypted: bytes, key: bytes) -> dict | None:
    try:
        return json.loads(Fernet(key).decrypt(encrypted).decode())
    except Exception:
        return None


def _load_env(env_path: Path | None) -> dict:
    path = env_path or Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return {}
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                creds[k.strip()] = v.strip().strip('"\'')
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = creds[k.strip()]
    return creds


def get_authorization_url(client_id: str, redirect_uri: str) -> str:
    return f"{AUTH_URL}?{urllib.parse.urlencode({'client_id': client_id, 'redirect_uri': redirect_uri})}"


def extract_code_from_redirect(redirect_url: str) -> str | None:
    parsed = urllib.parse.urlparse(redirect_url)
    qs = urllib.parse.parse_qs(parsed.query)
    codes = qs.get("code", [])
    return urllib.parse.unquote(codes[0]) if codes else None


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> dict:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


class SchwabSession:
    """Single OAuth session with background token refresh."""

    def __init__(
        self,
        session_name: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_path: Path,
        skill_dir: Path,
    ):
        self.session_name = session_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_path = token_path
        self.skill_dir = skill_dir
        self._tokens: dict | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def get_authorization_url(self) -> str:
        return get_authorization_url(self.client_id, self.redirect_uri)

    def complete_auth(self, redirect_url_or_code: str) -> dict:
        code = redirect_url_or_code
        if "code=" in redirect_url_or_code:
            code = extract_code_from_redirect(redirect_url_or_code) or redirect_url_or_code
        tokens = exchange_code(
            self.client_id, self.client_secret, code, self.redirect_uri
        )
        self._save_tokens(tokens)
        self._tokens = tokens
        self._start_refresh()
        return tokens

    def _save_tokens(self, tokens: dict) -> None:
        key = _get_encryption_key(self.client_secret)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_path, "wb") as f:
            f.write(_encrypt(tokens, key))

    def load_tokens(self) -> bool:
        if not self.token_path.exists():
            return False
        key = _get_encryption_key(self.client_secret)
        with open(self.token_path, "rb") as f:
            data = _decrypt(f.read(), key)
        if not data or "access_token" not in data:
            return False
        self._tokens = data
        return True

    def _refresh_loop(self) -> None:
        import logging
        log = logging.getLogger(f"schwab_auth.{self.session_name}")
        while not self._stop.wait(timeout=REFRESH_INTERVAL_SEC):
            with self._lock:
                t = self._tokens
            if t and "refresh_token" in t:
                try:
                    new = refresh_tokens(
                        self.client_id, self.client_secret, t["refresh_token"]
                    )
                    merged = {**t, **new}
                    with self._lock:
                        self._tokens = merged
                    self._save_tokens(merged)
                    log.debug("Token refresh succeeded for %s", self.session_name)
                except Exception as e:
                    # If network/DNS is unstable, avoid repeated refresh attempts.
                    maybe_trip_breaker(e, schwab_circuit)
                    log.warning("Token refresh failed for %s: %s", self.session_name, e)

    def _start_refresh(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

    def get_access_token(self) -> str | None:
        with self._lock:
            t = self._tokens
        if not t:
            if not self.load_tokens():
                return None
            self._start_refresh()
            with self._lock:
                t = self._tokens
        return t.get("access_token") if t else None

    def force_refresh(self) -> bool:
        """Refresh token now (for 401 recovery during long operations). Returns True if successful."""
        with self._lock:
            t = self._tokens
        if not t or "refresh_token" not in t:
            return False
        try:
            new = refresh_tokens(
                self.client_id, self.client_secret, t["refresh_token"]
            )
            merged = {**t, **new}
            with self._lock:
                self._tokens = merged
            self._save_tokens(merged)
            return True
        except Exception as e:
            # If we fail due to unstable connectivity, mark the circuit.
            try:
                maybe_trip_breaker(e, schwab_circuit)
            except Exception:
                pass
            return False

    def ensure_authenticated(self) -> str:
        token = self.get_access_token()
        if not token:
            raise RuntimeError(
                f"Not authenticated for {self.session_name}. "
                f"Run auth: open {self.get_authorization_url()}, then complete_auth(redirect_url)"
            )
        return token


class DualSchwabAuth:
    """
    Dual-session manager: Market Session + Account Session.
    """

    def __init__(self, skill_dir: Path | str | None = None):
        self.skill_dir = Path(skill_dir or Path(__file__).resolve().parent)
        env = _load_env(self.skill_dir / ".env")

        callback = env.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1").strip()

        market_key = env.get("SCHWAB_MARKET_APP_KEY", "")
        market_secret = env.get("SCHWAB_MARKET_APP_SECRET", "")
        account_key = env.get("SCHWAB_ACCOUNT_APP_KEY", "")
        account_secret = env.get("SCHWAB_ACCOUNT_APP_SECRET", "")

        self.market_session = SchwabSession(
            session_name="market",
            client_id=market_key,
            client_secret=market_secret,
            redirect_uri=callback,
            token_path=self.skill_dir / "tokens_market.enc",
            skill_dir=self.skill_dir,
        )
        self.account_session = SchwabSession(
            session_name="account",
            client_id=account_key,
            client_secret=account_secret,
            redirect_uri=callback,
            token_path=self.skill_dir / "tokens_account.enc",
            skill_dir=self.skill_dir,
        )

    def get_market_token(self) -> str:
        return self.market_session.ensure_authenticated()

    def get_account_token(self) -> str:
        return self.account_session.ensure_authenticated()

    def ensure_authenticated(self) -> str:
        """Alias for get_account_token for compatibility with guardrail client."""
        return self.get_account_token()
