"""Redis 客户端（redis_client.py）。

Redis 在本项目中承担：Celery 的 broker / 结果后端（见 ``src/tasks/celery_app.py``）、
以及轻量的运行时探活（/health 中的 redis 连通性）。这里提供同步客户端单例与
JSON 缓存读写小工具。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_redis() -> Any:
    """同步 Redis 客户端单例（decode_responses=True）。"""
    import redis

    client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
    logger.info("Redis 客户端已创建 url=%s", settings.redis.url)
    return client


def ping() -> bool:
    """探活：Redis 可用返回 True。"""
    try:
        return bool(get_redis().ping())
    except Exception as exc:  # noqa: BLE001 - 探活失败不应抛出
        logger.warning("Redis 探活失败: %s", exc)
        return False


def cache_set_json(key: str, value: Any, ttl: int | None = None) -> None:
    """写入 JSON 缓存。"""
    get_redis().set(key, json.dumps(value, ensure_ascii=False), ex=ttl or settings.redis.cache_ttl)


def cache_get_json(key: str) -> Any | None:
    """读取 JSON 缓存，未命中返回 None。"""
    raw = get_redis().get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001 - 脏数据不应影响业务
        return None
