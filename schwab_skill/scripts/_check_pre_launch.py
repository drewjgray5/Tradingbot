"""Pre-launch sanity check for the augmented multi-era reruns."""

from __future__ import annotations

import json
import pathlib
import subprocess


def main() -> int:
    root = pathlib.Path("schwab_skill/validation_artifacts")
    print("=== existing chunks for new run_ids (should be empty) ===")
    for tag in ("stage2_only_aug", "control_legacy_aug", "control_prod_default_aug"):
        d = root / "multi_era_chunks" / tag
        n = len(list(d.rglob("*.json"))) if d.exists() else 0
        print(f"  {tag}: {n} chunk files (dir exists={d.exists()})")

    print("=== existing aggregate artifacts for these tags ===")
    for tag in ("stage2_only_aug", "control_legacy_aug", "control_prod_default_aug"):
        p = root / f"multi_era_backtest_schwab_only_{tag}.json"
        print(f"  {p.name}: exists={p.exists()}")

    print("=== running python.exe processes ===")
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Depth 3 -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        data = json.loads(out.stdout) if out.stdout.strip() else []
    except Exception:
        data = []
    if isinstance(data, dict):
        data = [data]
    for p in data:
        cl = (p.get("CommandLine") or "")[:200]
        print(f"  PID {p.get('ProcessId')}: {cl}")
    print(f"  total python.exe procs: {len(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
