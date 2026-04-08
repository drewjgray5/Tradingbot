#!/usr/bin/env python3
"""
Lock current strategy profile into a timestamped artifact.

Only includes strategy-tuning keys (no secrets).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"

LOCK_KEYS = [
    "STAGE2_52W_PCT",
    "STAGE2_SMA_UPWARD_DAYS",
    "VCP_DAYS",
    "BREAKOUT_CONFIRM_ENABLED",
    "QUALITY_GATES_ENABLED",
    "QUALITY_GATES_MODE",
    "QUALITY_SOFT_MIN_REASONS",
    "QUALITY_MIN_SIGNAL_SCORE",
    "QUALITY_MIN_CONTINUATION_PROB",
    "QUALITY_MAX_BULL_TRAP_PROB",
    "QUALITY_REQUIRE_BREAKOUT_VOLUME",
    "SIGNAL_UNIVERSE_MODE",
    "SIGNAL_UNIVERSE_TARGET_SIZE",
    "QUALITY_WATCHLIST_PREFILTER_ENABLED",
    "QUALITY_WATCHLIST_PREFILTER_MAX",
]


def _read_env(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        vals[key.strip()] = value.strip().strip("\"'")
    return vals


def main() -> int:
    env_vals = _read_env(SKILL_DIR / ".env")
    locked = {k: env_vals.get(k) for k in LOCK_KEYS if k in env_vals}
    canonical = json.dumps(locked, sort_keys=True)
    profile_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile_hash_sha256": profile_hash,
        "locked_profile": locked,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / f"baseline_profile_lock_{run_id}.json"
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"Baseline profile lock artifact: {out}")
    print(f"profile_hash_sha256={profile_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
