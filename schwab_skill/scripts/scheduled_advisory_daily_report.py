#!/usr/bin/env python3
"""
Daily scheduled advisory model status report.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"


def main() -> int:
    cmd = [sys.executable, str(SCRIPTS_DIR / "report_advisory_status.py"), "--notify"]
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
