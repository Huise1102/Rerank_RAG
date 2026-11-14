import time
from collections import defaultdict

import redis.asyncio as redis

from src.core.config import settings

_memory_counters: dict[str, list[float]] = defaultdict(list)
_client: redis.Redis | None = None
_redis_failed = False


def _get_client() -> redis.Redis | None:
    global _client, _redis_failed
    if _redis_failed:
        return None
    if _client is None:
        _client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _client


def _memory_incr(key: str, window_seconds: int) -> int:
    now = time.time()
    timestamps = [item for item in _memory_counters[key] if now - item < window_seconds]
    timestamps.append(now)
    _memory_counters[key] = timestamps
    return len(timestamps)


def _memory_count(key: str, window_seconds: int) -> int:
    now = time.time()
    timestamps = [item for item in _memory_counters[key] if now - item < window_seconds]
    _memory_counters[key] = timestamps
    return len(timestamps)


async def increment_counter(key: str, window_seconds: int) -> int:
    global _redis_failed
    client = _get_client()
    if client is None:
        return _memory_incr(key, window_seconds)
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
        return int(count)
    except Exception:
        _redis_failed = True
        return _memory_incr(key, window_seconds)


async def get_counter(key: str, window_seconds: int) -> int:
    global _redis_failed
    client = _get_client()
    if client is None:
        return _memory_count(key, window_seconds)
    try:
        value = await client.get(key)
        return int(value) if value else 0
    except Exception:
        _redis_failed = True
        return _memory_count(key, window_seconds)


async def reset_counter(key: str) -> None:
    global _redis_failed
    client = _get_client()
    if client is None:
        _memory_counters.pop(key, None)
        return
    try:
        await client.delete(key)
    except Exception:
        _redis_failed = True
        _memory_counters.pop(key, None)
