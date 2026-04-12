"""Lightweight in-process counters for /metrics (no prometheus_client dependency)."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_counters: dict[str, int] = {}
_histogram_sums: dict[str, float] = {}
_histogram_counts: dict[str, int] = {}
_start = time.time()


def inc(name: str, value: int = 1) -> None:
    with _lock:
        _counters[name] = int(_counters.get(name, 0) or 0) + int(value)


def observe(name: str, value_sec: float) -> None:
    with _lock:
        _histogram_sums[name] = float(_histogram_sums.get(name, 0.0) or 0.0) + float(value_sec)
        _histogram_counts[name] = int(_histogram_counts.get(name, 0) or 0) + 1


def render_prometheus_text() -> str:
    with _lock:
        lines: list[str] = []
        lines.append("# HELP tradingbot_process_uptime_seconds Uptime of this API process.")
        lines.append("# TYPE tradingbot_process_uptime_seconds gauge")
        lines.append(f"tradingbot_process_uptime_seconds {time.time() - _start:.3f}")
        for k, v in sorted(_counters.items()):
            safe = k.replace("-", "_")
            lines.append(f"# HELP {safe} Counter {safe}")
            lines.append(f"# TYPE {safe} counter")
            lines.append(f"{safe} {int(v)}")
        for name in sorted(set(_histogram_sums.keys()) | set(_histogram_counts.keys())):
            s = float(_histogram_sums.get(name, 0.0) or 0.0)
            n = int(_histogram_counts.get(name, 0) or 0)
            safe = name.replace("-", "_")
            lines.append(f"# HELP {safe}_seconds_sum Sum of observed durations in seconds.")
            lines.append(f"# TYPE {safe}_seconds_sum counter")
            lines.append(f"{safe}_seconds_sum {s:.6f}")
            lines.append(f"# HELP {safe}_seconds_count Count of observations.")
            lines.append(f"# TYPE {safe}_seconds_count counter")
            lines.append(f"{safe}_seconds_count {n}")
        return "\n".join(lines) + "\n"


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "uptime_sec": round(time.time() - _start, 3),
            "counters": dict(_counters),
            "histograms": {
                k: {
                    "sum_sec": float(_histogram_sums.get(k, 0.0) or 0.0),
                    "count": int(_histogram_counts.get(k, 0) or 0),
                }
                for k in sorted(set(_histogram_sums.keys()) | set(_histogram_counts.keys()))
            },
        }
