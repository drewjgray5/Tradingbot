"""
Simple circuit breaker used to prevent repeated thrashing when network/DNS
is unstable (e.g. getaddrinfo failures) or reads time out.

When an unstable error occurs, we set `connection_stable=False` for a short
window so callers can quickly fall back instead of retrying per-ticker.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CircuitBreaker:
    name: str
    unstable_for_seconds: int = 300  # 5 minutes
    _stable_until_epoch: float = 0.0

    @property
    def connection_stable(self) -> bool:
        # stable unless we explicitly marked it unstable.
        return time.time() >= self._stable_until_epoch

    def mark_unstable(self) -> None:
        self._stable_until_epoch = time.time() + float(self.unstable_for_seconds)


schwab_circuit = CircuitBreaker(name="schwab", unstable_for_seconds=300)
discord_circuit = CircuitBreaker(name="discord", unstable_for_seconds=300)


def _message_contains(exc: Any, needle: str) -> bool:
    try:
        return needle.lower() in str(exc).lower()
    except Exception:
        return False


def is_getaddrinfo_or_readtimeout_error(exc: Any) -> bool:
    """
    True if exception looks like:
    - getaddrinfo failed / DNS resolution failure
    - ReadTimeout
    """
    if isinstance(exc, socket.gaierror):
        return True

    # requests/urllib3 error classes vary; use message heuristics too.
    if _message_contains(exc, "getaddrinfo failed"):
        return True
    if _message_contains(exc, "NameResolutionError".lower()):
        return True
    if _message_contains(exc, "Read timed out") or _message_contains(exc, "ReadTimeout"):
        return True

    # requests.exceptions.ReadTimeout inherits from requests.exceptions.RequestException,
    # but class name heuristics are safer than importing optional internals here.
    if exc.__class__.__name__ in ("ReadTimeout", "ReadTimeoutError"):
        return True
    return False


def maybe_trip_breaker(exc: Any, breaker: CircuitBreaker) -> bool:
    """
    If the error is an unstable network type, mark breaker unstable and return True.
    """
    if is_getaddrinfo_or_readtimeout_error(exc):
        breaker.mark_unstable()
        return True
    return False

