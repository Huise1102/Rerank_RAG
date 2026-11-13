import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.core.config import settings

logger = logging.getLogger(__name__)
_client: Redis | None = None


def get_client() -> Redis:
    """返回共享 Redis 客户端，连接参数来自环境变量。"""
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
        )
    return _client


async def ping() -> bool:
    """探测 Redis 是否可用，不可用时缓存整体降级。"""
    try:
        return bool(await get_client().ping())
    except (RedisError, OSError):
        return False


async def get_json(key: str) -> dict[str, Any] | None:
    try:
        raw = await get_client().get(key)
    except (RedisError, OSError) as exc:
        logger.warning("读取缓存失败：%s", exc)
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def set_json(key: str, value: dict[str, Any], ttl_seconds: int) -> bool:
    try:
        await get_client().set(key, json.dumps(value, ensure_ascii=False), ex=max(ttl_seconds, 1))
        return True
    except (RedisError, OSError) as exc:
        logger.warning("写入缓存失败：%s", exc)
        return False


async def delete(key: str) -> None:
    try:
        await get_client().delete(key)
    except (RedisError, OSError):
        return


async def scan_keys(pattern: str, batch: int = 200) -> list[str]:
    keys: list[str] = []
    try:
        async for key in get_client().scan_iter(match=pattern, count=batch):
            keys.append(key)
    except (RedisError, OSError):
        return []
    return keys


async def push_sample(key: str, value: dict[str, Any], limit: int = 50) -> None:
    try:
        client = get_client()
        await client.lpush("rag:cache:samples", json.dumps({"key": key, **value}, ensure_ascii=False))
        await client.ltrim("rag:cache:samples", 0, limit - 1)
    except (RedisError, OSError):
        return


async def list_samples(limit: int = 20) -> list[dict[str, Any]]:
    try:
        rows = await get_client().lrange("rag:cache:samples", 0, max(limit - 1, 0))
    except (RedisError, OSError):
        return []
    samples: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            samples.append(value)
    return samples


async def mark_sample_flagged(key: str) -> bool:
    """在样本列表中标记指定缓存键为误答，缓存值本身保留便于排查。"""
    client = get_client()
    try:
        rows = await client.lrange("rag:cache:samples", 0, -1)
        for index, row in enumerate(rows):
            try:
                value = json.loads(row)
            except (TypeError, ValueError):
                continue
            if value.get("key") != key:
                continue
            value["flagged"] = True
            await client.lset("rag:cache:samples", index, json.dumps(value, ensure_ascii=False))
            return True
    except (RedisError, OSError):
        return False
    return False


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except (RedisError, OSError):
            pass
        _client = None
