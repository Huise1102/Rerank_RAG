from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_admin
from src.db.models import AuditLog, User
from src.db.session import get_db
from src.services.cache_service import (
    cache_stats,
    clear_cache,
    get_cache_settings,
    semantic_supported,
    update_cache_settings,
)
from src.utils import cache

router = APIRouter(prefix="/admin/cache", tags=["缓存管理"])


class CacheSettingsRequest(BaseModel):
    enabled: bool | None = None
    semantic_enabled: bool | None = None
    ttl_days: int | None = Field(default=None, ge=1, le=30)
    semantic_threshold: float | None = Field(default=None, ge=0.75, le=1.0)


class CacheClearRequest(BaseModel):
    kb_id: int | None = None
    document_id: int | None = None


class CacheFlagRequest(BaseModel):
    key: str = Field(min_length=1, max_length=300)


async def _settings_payload(db: AsyncSession) -> dict[str, Any]:
    settings = await get_cache_settings(db)
    settings["semantic_supported"] = await semantic_supported()
    settings["redis_available"] = await cache.ping()
    return settings


@router.get("/settings")
async def read_cache_settings(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _settings_payload(db)


@router.patch("/settings")
async def write_cache_settings(
    payload: CacheSettingsRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    values = payload.model_dump(exclude_none=True)
    settings = await update_cache_settings(db, values)
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="update_cache_settings",
            target_type="system_setting",
            target_id="cache_settings",
            detail=(
                f"缓存开关 {settings['enabled']}，语义缓存 {settings['semantic_enabled']}，"
                f"有效期 {settings['ttl_days']} 天"
            ),
        )
    )
    await db.commit()
    settings["semantic_supported"] = await semantic_supported()
    settings["redis_available"] = await cache.ping()
    return settings


@router.get("/stats")
async def read_cache_stats(
    days: int = Query(default=7, ge=1, le=30),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stats = await cache_stats(db, days)
    stats["settings"] = await _settings_payload(db)
    return stats


@router.post("/clear")
async def clear_cache_entries(
    payload: CacheClearRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    removed = await clear_cache(payload.kb_id, payload.document_id)
    scope_text = "全部缓存"
    if payload.document_id is not None:
        scope_text = f"文档 {payload.document_id} 相关缓存"
    elif payload.kb_id is not None:
        scope_text = f"知识库 {payload.kb_id} 的缓存"
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="clear_cache",
            target_type="cache",
            target_id=str(payload.kb_id or payload.document_id or "all"),
            detail=f"清理{scope_text}，共 {removed} 条",
        )
    )
    await db.commit()
    return {"removed": removed}


@router.get("/samples")
async def list_cache_samples(
    limit: int = Query(default=20, ge=1, le=50),
    admin: User = Depends(require_admin),
):
    return {
        "samples": await cache.list_samples(limit),
        "redis_available": await cache.ping(),
    }


@router.post("/samples/flag")
async def flag_cache_sample(
    payload: CacheFlagRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    flagged = await cache.mark_sample_flagged(payload.key)
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="flag_cache_sample",
            target_type="cache",
            target_id=payload.key[:100],
            detail="标记缓存样本为疑似误答",
        )
    )
    await db.commit()
    return {"flagged": flagged}
