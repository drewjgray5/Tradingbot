"""
Schwab Developer API OAuth2 Authentication Module

Handles:
- Initial manual authorization URL for user redirect
- Authorization code exchange for tokens
- Background thread refreshing access token every 25 minutes
- Secure token storage in encrypted JSON
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

# Schwab OAuth endpoints
AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

# Default paths (relative to skill dir or explicit)
DEFAULT_TOKEN_FILE = "tokens.enc"
DEFAULT_ENV_FILE = ".env"
KEY_ENV_VAR = "SCHWAB_TOKEN_ENCRYPTION_KEY"


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive Fernet key from password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key


def _get_encryption_key(skill_dir: Path | None = None) -> bytes:
    """Get Fernet key from env or generate from machine-identifiable source."""
    key_b64 = os.environ.get(KEY_ENV_VAR)
    if key_b64:
        return key_b64.encode() if isinstance(key_b64, str) else key_b64
    # Fallback: derive from a fixed salt + optional skill path
    salt = b"schwab_token_v1"
    secret = os.environ.get("SCHWAB_APP_SECRET", "schwab_fallback_secret")
    return _derive_key(secret, salt)


def _encrypt_tokens(data: dict, key: bytes) -> bytes:
    """Encrypt token dict to bytes."""
    f = Fernet(key)
    return f.encrypt(json.dumps(data).encode())


def _decrypt_tokens(encrypted: bytes, key: bytes) -> dict | None:
    """Decrypt bytes to token dict."""
    try:
        f = Fernet(key)
        return json.loads(f.decrypt(encrypted).decode())
    except Exception:
        return None


def load_env_creds(env_path: str | Path | None = None) -> dict:
    """Load client_id and client_secret from .env-style file."""
    path = Path(env_path or DEFAULT_ENV_FILE)
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
                val = v.strip().strip('"\'')
                creds[k.strip()] = val
                # Populate os.environ for encryption key derivation
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = val
    return creds


def get_authorization_url(
    client_id: str,
    redirect_uri: str = "https://127.0.0.1",
) -> str:
    """
    Build the manual authorization URL. User must open this in a browser,
    log in, and copy the full redirect URL (with ?code=...) back.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def extract_code_from_redirect_url(redirect_url: str) -> str | None:
    """
    Extract and URL-decode the authorization code from the redirect URL.
    Schwab requires the code to be URL-decoded before use (e.g. %40 -> @).
    """
    parsed = urllib.parse.urlparse(redirect_url)
    qs = urllib.parse.parse_qs(parsed.query)
    codes = qs.get("code", [])
    if not codes:
        return None
    return urllib.parse.unquote(codes[0])


def exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str = "https://127.0.0.1",
) -> dict:
    """
    Exchange authorization code for access_token and refresh_token.
    Returns the full token response dict.
    """
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if not schwab_circuit.connection_stable:
        raise RuntimeError("Schwab connection unstable (circuit breaker)")
    try:
        resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=60)
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        raise
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Refresh access token using refresh_token."""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if not schwab_circuit.connection_stable:
        raise RuntimeError("Schwab connection unstable (circuit breaker)")
    try:
        resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=60)
    except Exception as e:
        maybe_trip_breaker(e, schwab_circuit)
        raise
    resp.raise_for_status()
    return resp.json()


class SchwabAuth:
    """
    Schwab OAuth2 auth manager with:
    - Manual URL for initial auth
    - Background refresh every 25 minutes
    - Encrypted token storage
    """

    REFRESH_INTERVAL_SEC = 25 * 60  # 25 minutes

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str = "https://127.0.0.1",
        token_path: str | Path | None = None,
        env_path: str | Path | None = None,
        skill_dir: Path | None = None,
    ):
        self.redirect_uri = redirect_uri
        self.skill_dir = Path(skill_dir or os.getcwd())
        self.token_path = Path(token_path or self.skill_dir / DEFAULT_TOKEN_FILE)
        env = load_env_creds(env_path or self.skill_dir / DEFAULT_ENV_FILE)
        self.client_id = client_id or env.get("SCHWAB_CLIENT_ID", "")
        self.client_secret = client_secret or env.get("SCHWAB_APP_SECRET", env.get("SCHWAB_CLIENT_SECRET", ""))
        self._tokens: dict | None = None
        self._lock = threading.Lock()
        self._refresh_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def get_authorization_url(self) -> str:
        """Return URL for user to open in browser."""
        return get_authorization_url(self.client_id, self.redirect_uri)

    def complete_initial_auth(self, redirect_url_or_code: str) -> dict:
        """
        Complete initial auth. Pass either:
        - Full redirect URL (e.g. https://127.0.0.1/?code=...)
        - Or the raw authorization code
        """
        code = redirect_url_or_code
        if "code=" in redirect_url_or_code:
            code = extract_code_from_redirect_url(redirect_url_or_code) or redirect_url_or_code
        tokens = exchange_code_for_tokens(
            self.client_id,
            self.client_secret,
            code,
            self.redirect_uri,
        )
        self._save_tokens(tokens)
        self._tokens = tokens
        self._start_refresh_thread()
        return tokens

    def load_tokens(self) -> bool:
        """Load tokens from encrypted file. Returns True if valid tokens loaded."""
        if not self.token_path.exists():
            return False
        key = _get_encryption_key(self.skill_dir)
        with open(self.token_path, "rb") as f:
            data = _decrypt_tokens(f.read(), key)
        if not data or "access_token" not in data:
            return False
        self._tokens = data
        return True

    def _save_tokens(self, tokens: dict) -> None:
        """Persist tokens to encrypted file."""
        key = _get_encryption_key(self.skill_dir)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_path, "wb") as f:
            f.write(_encrypt_tokens(tokens, key))

    def _refresh(self) -> None:
        """Refresh access token and save."""
        with self._lock:
            tokens = self._tokens
        if not tokens or "refresh_token" not in tokens:
            return
        try:
            new = refresh_access_token(
                self.client_id,
                self.client_secret,
                tokens["refresh_token"],
            )
            # Schwab may return new refresh_token; keep existing if not present
            merged = {**tokens, **new}
            with self._lock:
                self._tokens = merged
            self._save_tokens(merged)
        except Exception:
            pass  # Log in production

    def _refresh_loop(self) -> None:
        """Background loop: refresh every 25 minutes."""
        while not self._stop_event.wait(timeout=self.REFRESH_INTERVAL_SEC):
            self._refresh()

    def _start_refresh_thread(self) -> None:
        """Start background refresh thread."""
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._stop_event.clear()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()

    def get_access_token(self) -> str | None:
        """Return current access token, refreshing if needed."""
        with self._lock:
            tokens = self._tokens
        if not tokens:
            if not self.load_tokens():
                return None
            self._start_refresh_thread()
            with self._lock:
                tokens = self._tokens
        return tokens.get("access_token") if tokens else None

    def ensure_authenticated(self) -> str:
        """
        Ensure we have a valid access token. Raises RuntimeError if not.
        Call this before any API request.
        """
        token = self.get_access_token()
        if not token:
            url = self.get_authorization_url()
            raise RuntimeError(
                "Not authenticated. Run initial auth:\n"
                f"1. Open: {url}\n"
                "2. Log in and approve\n"
                "3. Call complete_initial_auth(redirect_url) with the full redirect URL"
            )
        return token

    def stop(self) -> None:
        """Stop the refresh thread."""
        self._stop_event.set()
