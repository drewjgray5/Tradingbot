#!/usr/bin/env python3
"""Append-only signed promotion ledger.

Replaces the ad-hoc ``MANUAL_PROMOTION_APPROVED=1`` env knob with a tamper-
evident JSONL ledger at ``schwab_skill/scripts/promotion_ledger.jsonl``. Every
strategy / model / plugin promotion writes one line with:

    * monotonic sequence number
    * UTC timestamp
    * target identifier (e.g. ``EXEC_QUALITY_MODE=live``)
    * approver (defaults to ``$USERNAME`` / ``$USER``)
    * SHA-256 of the previous line + the current payload
      (``prev_sig`` chains to the previous entry, ``sig`` covers everything)

Validation scripts can verify the chain (``verify`` subcommand) and refuse to
apply the requested change unless the chain is intact.

CLI:
    python scripts/promotion_ledger.py append --target EXEC_QUALITY_MODE=live --reason "Sprint 3 promotion"
    python scripts/promotion_ledger.py tail --n 20
    python scripts/promotion_ledger.py verify

Env:
    PROMOTION_APPROVER -- override the approver name written into the ledger.
    PROMOTION_LEDGER_PATH -- override the ledger file path (mostly for tests).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LEDGER = Path(__file__).resolve().parent / "promotion_ledger.jsonl"


def default_ledger_path() -> Path:
    """Resolve the active ledger path. Honours ``PROMOTION_LEDGER_PATH``
    so tests can redirect writes without touching the real ledger."""
    p = os.environ.get("PROMOTION_LEDGER_PATH")
    return Path(p) if p else DEFAULT_LEDGER


# Back-compat alias — the original private name is still used inside
# this module and by older imports during the #9 transition.
_ledger_path = default_ledger_path


def read_entries(path: Path | None = None) -> list[dict[str, Any]]:
    """Return every parseable JSONL entry from the ledger.

    Silently skips malformed lines (``verify`` will flag them); this lets
    the consumer-side guard treat an unreadable line as "no approval"
    rather than crashing the apply flow."""
    target_path = path or default_ledger_path()
    if not target_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with target_path.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return out


# Back-compat alias for the previous private name.
_read_lines = read_entries


def _digest(prev_sig: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_sig + blob).encode("utf-8")).hexdigest()


def verify_chain(entries: list[dict[str, Any]]) -> tuple[bool, str]:
    """Re-verify the SHA-256 chain integrity of ``entries``.

    Returns ``(True, "ok: N entries")`` when every signature matches and
    every ``prev_sig`` chains to the previous row's ``sig``. Otherwise
    returns ``(False, reason)`` describing the first offending row so the
    operator can repair (or reject the apply attempt).

    Used both by the CLI ``verify`` subcommand and by
    ``promotion_guard.ensure_signed_approval`` so consumers can refuse a
    promotion if the audit trail has been tampered with.
    """
    if not entries:
        return True, "ledger empty"
    expected_prev = ""
    for idx, row in enumerate(entries, start=1):
        prev_sig = str(row.get("prev_sig") or "")
        sig = str(row.get("sig") or "")
        if prev_sig != expected_prev:
            return False, f"row {idx}: prev_sig mismatch (target={row.get('target')!r})"
        sigless = {k: v for k, v in row.items() if k != "sig"}
        recomputed = _digest(prev_sig, sigless)
        if recomputed != sig:
            return False, f"row {idx}: sig mismatch (target={row.get('target')!r})"
        expected_prev = sig
    return True, f"ok: {len(entries)} entries"


def _approver() -> str:
    explicit = os.environ.get("PROMOTION_APPROVER", "").strip()
    if explicit:
        return explicit
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def cmd_append(args: argparse.Namespace) -> int:
    path = default_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_lines(path)
    seq = (existing[-1]["seq"] + 1) if existing else 1
    prev_sig = existing[-1]["sig"] if existing else ""
    payload = {
        "seq": seq,
        "ts": datetime.now(timezone.utc).isoformat(),
        "target": str(args.target),
        "reason": str(args.reason or ""),
        "approver": _approver(),
        "prev_sig": prev_sig,
    }
    payload["sig"] = _digest(prev_sig, payload)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    path = default_ledger_path()
    rows = read_entries(path)
    if not rows:
        print(f"No promotion ledger entries at {path}")
        return 0
    n = max(1, int(args.n or 20))
    for row in rows[-n:]:
        print(json.dumps(row))
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    path = default_ledger_path()
    rows = read_entries(path)
    if not rows:
        print(f"Ledger empty: {path}")
        return 0
    ok, message = verify_chain(rows)
    if not ok:
        print(f"BAD CHAIN: {message}")
        return 1
    print(f"OK: {len(rows)} entries, chain intact")
    return 0


def find_recent_approval(
    target: str,
    *,
    max_age_hours: float = 24.0,
    entries: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the most recent ledger entry approving ``target`` within
    ``max_age_hours``, or ``None`` if no fresh approval exists.

    A "match" is an exact string equality on ``target``. The window is
    checked against the entry's ``ts`` field (UTC ISO-8601). Caller is
    responsible for chain verification — this function only filters.

    Used by ``promotion_guard.ensure_signed_approval`` to decide whether
    an ``--apply`` flow is allowed to proceed.
    """
    rows = entries if entries is not None else read_entries()
    if not rows:
        return None
    now_dt = now or datetime.now(timezone.utc)
    cutoff = now_dt.timestamp() - float(max_age_hours) * 3600.0
    for row in reversed(rows):
        if str(row.get("target") or "") != target:
            continue
        ts_raw = str(row.get("ts") or "")
        try:
            entry_dt = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        if entry_dt.timestamp() < cutoff:
            return None
        return row
    return None


def cmd_check(args: argparse.Namespace) -> int:
    """CLI for operator pre-flight: ``promotion_ledger.py check --target X``
    returns exit code 0 if a fresh signed approval exists, 1 otherwise.
    Useful to wire into shell aliases / pre-commit hooks before running
    ``--apply``."""
    rows = read_entries()
    ok, message = verify_chain(rows)
    if not ok:
        print(f"FAIL: chain invalid ({message})")
        return 1
    found = find_recent_approval(
        args.target, max_age_hours=float(args.max_age_hours), entries=rows
    )
    if not found:
        print(
            f"FAIL: no approval for {args.target!r} within "
            f"{args.max_age_hours}h. Append one with:\n"
            f"  python scripts/promotion_ledger.py append "
            f"--target {args.target} --reason '<why>'"
        )
        return 1
    print(
        f"OK: approval seq={found.get('seq')} by {found.get('approver')!r} "
        f"at {found.get('ts')}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_app = sub.add_parser("append", help="Append a new promotion entry")
    p_app.add_argument("--target", required=True, help="e.g. EXEC_QUALITY_MODE=live or advisory_v2_2026Q2")
    p_app.add_argument("--reason", default="", help="Free-form justification")
    p_app.set_defaults(func=cmd_append)

    p_tail = sub.add_parser("tail", help="Show the most recent N entries")
    p_tail.add_argument("--n", type=int, default=20)
    p_tail.set_defaults(func=cmd_tail)

    p_ver = sub.add_parser("verify", help="Verify the chain of signatures")
    p_ver.set_defaults(func=cmd_verify)

    p_chk = sub.add_parser(
        "check",
        help="Exit 0 iff a recent signed approval for --target exists and the chain is intact",
    )
    p_chk.add_argument("--target", required=True)
    p_chk.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="Reject approvals older than this many hours (default: 24)",
    )
    p_chk.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
