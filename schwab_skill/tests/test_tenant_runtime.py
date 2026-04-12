"""SaaS tenant skill directory materialization."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from schwab_auth import write_encrypted_token_file
from webapp.db import Base
from webapp.models import User, UserCredential
from webapp.security import encrypt_secret
from webapp.tenant_runtime import (
    materialize_tenant_skill_dir,
    user_can_materialize_for_scan,
    user_has_account_session,
)


@pytest.fixture
def cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)


@pytest.fixture
def schwab_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_MARKET_APP_KEY", "mk")
    monkeypatch.setenv("SCHWAB_MARKET_APP_SECRET", "msecret")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_KEY", "ak")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "asecret")


@pytest.fixture
def db_session(cred_key: None) -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory()


def test_user_has_account_session_legacy_tokens(
    db_session: Session, cred_key: None, schwab_platform_env: None
) -> None:
    db = db_session
    db.add(User(id="u1", email="a@b.c", auth_provider="supabase"))
    db.add(
        UserCredential(
            user_id="u1",
            access_token_enc=encrypt_secret("acc"),
            refresh_token_enc=encrypt_secret("ref"),
            token_type="Bearer",
        )
    )
    db.commit()
    assert user_has_account_session(db, "u1") is True


def test_materialize_dual_payloads(
    tmp_path: Path,
    db_session: Session,
    cred_key: None,
    schwab_platform_env: None,
) -> None:
    db = db_session
    db.add(User(id="u2", email="x@y.z", auth_provider="supabase"))
    market_json = json.dumps({"access_token": "m1", "refresh_token": "mr1"})
    account_json = json.dumps({"access_token": "a1", "refresh_token": "ar1"})
    db.add(
        UserCredential(
            user_id="u2",
            market_token_payload_enc=encrypt_secret(market_json),
            account_token_payload_enc=encrypt_secret(account_json),
        )
    )
    db.commit()

    skill_dir = tmp_path / "tenant"
    materialize_tenant_skill_dir(db, "u2", skill_dir)

    assert (skill_dir / ".env").is_file()
    assert (skill_dir / "tokens_market.enc").is_file()
    assert (skill_dir / "tokens_account.enc").is_file()


def test_materialize_shared_platform_market(
    tmp_path: Path,
    db_session: Session,
    cred_key: None,
    schwab_platform_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform = tmp_path / "platform"
    platform.mkdir()
    write_encrypted_token_file(
        platform / "tokens_market.enc",
        {"access_token": "pm", "refresh_token": "pr"},
        "msecret",
    )
    monkeypatch.setenv("SAAS_PLATFORM_MARKET_SKILL_DIR", str(platform))

    db = db_session
    db.add(User(id="u3", email="p@q.r", auth_provider="supabase"))
    account_json = json.dumps({"access_token": "a2", "refresh_token": "ar2"})
    db.add(
        UserCredential(
            user_id="u3",
            account_token_payload_enc=encrypt_secret(account_json),
        )
    )
    db.commit()

    assert user_can_materialize_for_scan(db, "u3")[0] is True

    skill_dir = tmp_path / "t2"
    materialize_tenant_skill_dir(db, "u3", skill_dir)
    assert (skill_dir / "tokens_market.enc").is_file()
    assert (skill_dir / "tokens_account.enc").is_file()


def test_materialize_appends_user_trading_halted(
    tmp_path: Path,
    db_session: Session,
    cred_key: None,
    schwab_platform_env: None,
) -> None:
    db = db_session
    db.add(User(id="u4", email="halt@example.com", auth_provider="supabase", trading_halted=True))
    market_json = json.dumps({"access_token": "m1", "refresh_token": "mr1"})
    account_json = json.dumps({"access_token": "a1", "refresh_token": "ar1"})
    db.add(
        UserCredential(
            user_id="u4",
            market_token_payload_enc=encrypt_secret(market_json),
            account_token_payload_enc=encrypt_secret(account_json),
        )
    )
    db.commit()
    skill_dir = tmp_path / "halt"
    materialize_tenant_skill_dir(db, "u4", skill_dir)
    env_text = (skill_dir / ".env").read_text(encoding="utf-8")
    assert "USER_TRADING_HALTED=1" in env_text
