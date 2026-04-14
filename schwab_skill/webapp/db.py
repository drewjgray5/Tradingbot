from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "webapp.db"


def _strip_invalid_host_brackets(url: str) -> str:
    """Unwrap [host] when brackets wrap a normal hostname instead of IPv6."""
    try:
        urlparse(url)
    except ValueError:
        if "[" not in url:
            return url
        scheme, sep, tail = url.partition("://")
        if not sep:
            return url
        userinfo = ""
        host_port_path = tail
        if "@" in tail:
            userinfo, _, host_port_path = tail.rpartition("@")
        if not host_port_path.startswith("[") or "]" not in host_port_path:
            return url
        host, sep, suffix = host_port_path[1:].partition("]")
        if not sep:
            return url
        if ":" in host:
            return url
        auth = f"{userinfo}@" if userinfo else ""
        return f"{scheme}://{auth}{host}{suffix}"
    except Exception:
        return url
    return url
def _reject_http_database_url(url: str) -> None:
    """
    SQLAlchemy treats the URL scheme as a dialect name. If DATABASE_URL is mistakenly set to
    a Supabase REST URL (https://...supabase.co) or any https:// URL, you get:
    NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:https
    """
    u = (url or "").strip()
    if u.startswith(("http://", "https://")):
        raise RuntimeError(
            "DATABASE_URL must be a SQLAlchemy database URI (e.g. postgresql+psycopg2://... or "
            "sqlite:///...), not an HTTP(S) API URL. On Supabase use Settings, Database, "
            "Connection string (URI) or the pooler URI; on Render use your Postgres service "
            "Internal or External Database URL. SUPABASE_URL (https://...) belongs in SUPABASE_URL, "
            "not DATABASE_URL."
        )
def _normalize_database_url(url: str) -> str:
    """Render/Heroku often use postgres://; SQLAlchemy 2 + psycopg2 expect postgresql+psycopg2://."""
    u = _strip_invalid_host_brackets(url.strip())
    if u.startswith("sqlite"):
        return u
    if u.startswith("postgres://"):
        return "postgresql+psycopg2://" + u[len("postgres://") :]
    if u.startswith("postgresql://") and not u.split("://", 1)[0].endswith("psycopg2"):
        return "postgresql+psycopg2://" + u[len("postgresql://") :]
    if u.startswith(("http://", "https://")):
        # Some hosts copy/paste or rewrite DSNs with an http(s) scheme. If the
        # value otherwise looks like a Postgres DSN, coerce it safely.
        try:
            parsed = urlparse(u)
            has_db_name = bool(parsed.path and parsed.path != "/")
            if parsed.username and parsed.hostname and has_db_name:
                return urlunparse(
                    (
                        "postgresql+psycopg2",
                        parsed.netloc,
                        parsed.path,
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
        except Exception:
            return u
    return u


def _validate_database_url(url: str) -> str:
    sanitized_url = _strip_invalid_host_brackets(url)
    scheme = sanitized_url.split("://", 1)[0].strip().lower() if "://" in sanitized_url else ""
    if scheme.startswith("sqlite") or scheme.startswith("postgresql"):
        return sanitized_url
    if scheme in {"http", "https"}:
        raise ValueError(
            "Invalid DATABASE_URL: got an http(s) URL. Use a Postgres DSN "
            "(postgres:// or postgresql://), or Render's database "
            "connectionString env binding."
        )
    return sanitized_url


def _maybe_require_ssl_for_render(url: str) -> str:
    """Render Postgres often needs sslmode=require; missing it causes OperationalError (sqlalche.me/e/20/e3q8)."""
    if not url.startswith("postgresql"):
        return url
    flag = (os.getenv("DATABASE_SSLMODE") or "").strip().lower()
    if flag in ("disable", "0", "false", "off", "no"):
        return url
    if "sslmode=" in url:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return url
    if not host.endswith(".render.com"):
        return url
    q = list(parse_qsl(parsed.query, keep_blank_values=True))
    if not any(k == "sslmode" for k, _ in q):
        q.append(("sslmode", "require"))
    new_query = urlencode(q)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


_raw_db_url = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}")
DATABASE_URL = _validate_database_url(
    _maybe_require_ssl_for_render(_normalize_database_url(_raw_db_url))
)
_reject_http_database_url(DATABASE_URL)

engine_kwargs: dict[str, object] = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_pre_ping=True,
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    )

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

