"""MinIO 客户端（minio_client.py）。

仅在 ``RAG_STORAGE__TYPE=minio`` 时使用；客户端与 bucket 均惰性创建并缓存，
避免应用启动即强制依赖 MinIO 可用（毕设默认走本地存储）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_minio_client() -> Any:
    from minio import Minio

    cfg = settings.minio
    client = Minio(
        cfg.endpoint,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        secure=cfg.secure,
    )
    logger.info("MinIO 客户端已创建 endpoint=%s bucket=%s", cfg.endpoint, cfg.bucket)
    return client


def ensure_bucket() -> None:
    """确保 bucket 存在（幂等）。"""
    client = get_minio_client()
    bucket = settings.minio.bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("已创建 MinIO bucket: %s", bucket)
