"""Tests for the signed-ledger promotion guard added in #9.

Covers ``promotion_guard.ensure_signed_approval`` and the underlying
``promotion_ledger`` chain helpers it relies on. Each test redirects
``PROMOTION_LEDGER_PATH`` to a tmp-dir ledger so the real
``scripts/promotion_ledger.jsonl`` is never touched.
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"


@pytest.fixture
def ledger_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Load fresh copies of ``promotion_ledger`` and ``promotion_guard``
    against a tmp ledger path. Re-imported per-test so cached state from
    the lazy ``_load_ledger_helpers`` call doesn't leak between cases."""
    ledger_path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("PROMOTION_LEDGER_PATH", str(ledger_path))
    monkeypatch.delenv("MANUAL_PROMOTION_APPROVED", raising=False)
    monkeypatch.delenv("PROMOTION_LEDGER_SKIP", raising=False)

    if str(SCRIPTS_DIR) not in sys.path:
        monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    for name in ("promotion_ledger", "promotion_guard"):
        sys.modules.pop(name, None)
    ledger = importlib.import_module("promotion_ledger")
    guard = importlib.import_module("promotion_guard")
    yield ledger, guard, ledger_path
    for name in ("promotion_ledger", "promotion_guard"):
        sys.modules.pop(name, None)


def _append(ledger, target: str, *, reason: str = "test") -> dict:
    """Drive the CLI append helper so the fixture's signature chain
    stays exercised exactly the way operators would write it."""
    import argparse

    args = argparse.Namespace(target=target, reason=reason)
    rc = ledger.cmd_append(args)
    assert rc == 0
    return ledger.read_entries()[-1]


def test_no_apply_requested_is_a_noop(ledger_modules) -> None:
    _ledger, guard, _path = ledger_modules
    assert guard.ensure_signed_approval("anything", apply_requested=False) is True


def test_apply_requires_recent_approval(ledger_modules, capsys) -> None:
    _ledger, guard, _path = ledger_modules
    assert (
        guard.ensure_signed_approval("strategy_champion_params", apply_requested=True)
        is False
    )
    out = capsys.readouterr().out
    assert "no signed approval" in out
    assert "promotion_ledger.py append" in out
    assert "strategy_champion_params" in out


def test_recent_signed_approval_unblocks_apply(ledger_modules, capsys) -> None:
    ledger, guard, _path = ledger_modules
    _append(ledger, "advisory_model", reason="green walk-forward run")
    assert (
        guard.ensure_signed_approval("advisory_model", apply_requested=True) is True
    )
    out = capsys.readouterr().out
    assert "Signed approval accepted" in out
    assert "advisory_model" in out


def test_stale_approval_is_rejected(ledger_modules) -> None:
    """An approval older than ``max_age_hours`` must not unblock the
    apply. We can't simply rewrite the ``ts`` field on the raw ledger
    line (that would invalidate the signature, which is a different
    failure mode), so we exercise the staleness logic directly by
    appending a fresh row and then asking ``find_recent_approval`` to
    evaluate it from a future point in time."""
    ledger, _guard, _path = ledger_modules
    _append(ledger, "advisory_model")
    rows = ledger.read_entries()
    future = datetime.now(timezone.utc) + timedelta(hours=25)
    found = ledger.find_recent_approval(
        "advisory_model", max_age_hours=24.0, entries=rows, now=future
    )
    assert found is None, "25h-old approval should be filtered out by 24h window"

    fresh = ledger.find_recent_approval(
        "advisory_model",
        max_age_hours=48.0,
        entries=rows,
        now=future,
    )
    assert fresh is not None, "expanding window to 48h should re-admit the entry"


def test_target_mismatch_is_rejected(ledger_modules) -> None:
    ledger, guard, _path = ledger_modules
    _append(ledger, "advisory_model")
    assert (
        guard.ensure_signed_approval(
            "strategy_champion_params", apply_requested=True
        )
        is False
    )


def test_broken_chain_blocks_even_with_matching_entry(
    ledger_modules, capsys
) -> None:
    """A tampered ``sig`` field must block the apply outright, regardless
    of whether a freshly-targeted row exists. This is the core safety
    property of the signed ledger — the env-knob fallback should NOT
    rescue this case."""
    ledger, guard, ledger_path = ledger_modules
    _append(ledger, "advisory_model")
    # Tamper: rewrite the last line's signature.
    raw = ledger_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(raw[-1])
    row["sig"] = "0" * 64
    raw[-1] = json.dumps(row, sort_keys=True)
    ledger_path.write_text("\n".join(raw) + "\n", encoding="utf-8")

    assert (
        guard.ensure_signed_approval("advisory_model", apply_requested=True)
        is False
    )
    out = capsys.readouterr().out
    assert "chain is broken" in out


def test_legacy_env_fallback_still_works_with_warning(
    ledger_modules, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _ledger, guard, _path = ledger_modules
    monkeypatch.setenv("MANUAL_PROMOTION_APPROVED", "1")
    assert (
        guard.ensure_signed_approval("advisory_model", apply_requested=True) is True
    )
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "deprecated" in out
    assert "MANUAL_PROMOTION_APPROVED" in out


def test_legacy_env_fallback_disabled_when_caller_opts_out(
    ledger_modules, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger, guard, _path = ledger_modules
    monkeypatch.setenv("MANUAL_PROMOTION_APPROVED", "1")
    assert (
        guard.ensure_signed_approval(
            "advisory_model", apply_requested=True, allow_legacy_env=False
        )
        is False
    )


def test_skip_env_short_circuits_for_local_dev(
    ledger_modules, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _ledger, guard, _path = ledger_modules
    monkeypatch.setenv("PROMOTION_LEDGER_SKIP", "1")
    assert (
        guard.ensure_signed_approval("advisory_model", apply_requested=True) is True
    )
    out = capsys.readouterr().out
    assert "PROMOTION_LEDGER_SKIP=1" in out
    assert "Do not use in CI" in out


def test_check_subcommand_exit_codes(
    ledger_modules, capsys
) -> None:
    """``promotion_ledger.py check --target X`` is the operator
    pre-flight. Should exit 0 with a fresh approval, 1 without."""
    ledger, _guard, _path = ledger_modules
    import argparse

    miss_args = argparse.Namespace(target="advisory_model", max_age_hours=24.0)
    assert ledger.cmd_check(miss_args) == 1
    assert "FAIL" in capsys.readouterr().out

    _append(ledger, "advisory_model")
    hit_args = argparse.Namespace(target="advisory_model", max_age_hours=24.0)
    assert ledger.cmd_check(hit_args) == 0
    assert "OK" in capsys.readouterr().out


def test_back_compat_alias_for_manual_env(
    ledger_modules, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy ``ensure_manual_promotion_approval`` keeps its old
    behaviour so any out-of-tree callers don't break mid-transition."""
    _ledger, guard, _path = ledger_modules
    assert guard.ensure_manual_promotion_approval(False) is True
    assert guard.ensure_manual_promotion_approval(True) is False
    monkeypatch.setenv("MANUAL_PROMOTION_APPROVED", "1")
    assert guard.ensure_manual_promotion_approval(True) is True


def test_chain_integrity_after_multiple_appends(ledger_modules) -> None:
    ledger, _guard, _path = ledger_modules
    for i in range(5):
        _append(ledger, f"target_{i}", reason=f"step {i}")
    rows = ledger.read_entries()
    assert [r["seq"] for r in rows] == [1, 2, 3, 4, 5]
    ok, msg = ledger.verify_chain(rows)
    assert ok, msg

    # Any single-byte tamper should break the chain.
    rows[2]["reason"] = "modified"
    ok, msg = ledger.verify_chain(rows)
    assert not ok
    assert "row 3" in msg
