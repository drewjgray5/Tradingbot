from __future__ import annotations

import os
import time

import redis

_client: redis.Redis | None = None


def redis_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def redis_ping() -> bool:
    try:
        return bool(redis_client().ping())
    except redis.RedisError:
        return False


def acquire_scan_cooldown(user_id: str, cooldown_sec: int) -> bool:
    """Return True if scan may proceed (key was absent). False if within cooldown."""
    try:
        key = f"saas:scan:cooldown:{user_id}"
        return bool(redis_client().set(key, "1", nx=True, ex=cooldown_sec))
    except redis.RedisError:
        fail_open = (os.getenv("SAAS_RATE_LIMIT_FAIL_OPEN") or "").strip().lower() in ("1", "true", "yes", "on")
        return bool(fail_open)


def fixed_window_rate_limit(user_id: str, bucket: str, limit: int, window_sec: int) -> tuple[bool, int]:
    """
    Return (allowed, current_count). On Redis failure default to deny (fail-closed).
    """
    try:
        r = redis_client()
        window_id = int(time.time()) // max(1, window_sec)
        key = f"saas:rl:{bucket}:{user_id}:{window_id}"
        n = int(r.incr(key))
        if n == 1:
            r.expire(key, window_sec)
        return n <= limit, n
    except redis.RedisError:
        fail_open = (os.getenv("SAAS_RATE_LIMIT_FAIL_OPEN") or "").strip().lower() in ("1", "true", "yes", "on")
        return bool(fail_open), 0


def order_idempotency_existing_task(user_id: str, idempotency_key: str) -> str | None:
    try:
        key = f"saas:idem:order:{user_id}:{idempotency_key}"
        v = redis_client().get(key)
        return str(v) if v else None
    except redis.RedisError:
        return None


def order_idempotency_record_task(
    user_id: str, idempotency_key: str, task_id: str, ttl_sec: int = 86400
) -> None:
    try:
        key = f"saas:idem:order:{user_id}:{idempotency_key}"
        redis_client().set(key, task_id, ex=ttl_sec)
    except redis.RedisError:
        pass
