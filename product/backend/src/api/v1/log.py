"""日志路由（log.py）：检索日志（DB 分页）与系统日志（logs/app.log 尾部）。"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logging import clear_log_file, get_logger
from src.db.models.retrieval_log import RetrievalLog
from src.db.session import get_db
from src.schemas.log import LogsPage, RetrievalLogOut

logger = get_logger(__name__)

router = APIRouter(tags=["日志"])


@router.get("/logs/retrieval", response_model=LogsPage, summary="检索日志（分页）")
async def retrieval_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    kb_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> LogsPage:
    count_stmt = select(func.count()).select_from(RetrievalLog)
    stmt = select(RetrievalLog)
    if kb_id is not None:
        count_stmt = count_stmt.where(RetrievalLog.kb_id == kb_id)
        stmt = stmt.where(RetrievalLog.kb_id == kb_id)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = await db.execute(
        stmt.order_by(RetrievalLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = [RetrievalLogOut.model_validate(r) for r in rows.scalars().all()]
    return LogsPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/logs/system", summary="系统日志（app.log 尾部）")
def system_logs(
    lines: int = Query(default=200, ge=1, le=5000),
    level: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> dict:
    """读取 logs/app.log 尾部若干行，支持按级别 / 关键字过滤。"""
    path: Path = settings.log.dir / settings.log.file_name
    if not path.exists():
        return {"lines": [], "path": str(path), "exists": False}

    lvl = (level or "").strip().upper()
    kw = (keyword or "").strip()
    kept: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if lvl and lvl not in line:
                continue
            if kw and kw not in line:
                continue
            kept.append(line)
    # 最新的一行在最前，便于日志窗口自上而下按时间倒序阅读
    return {"lines": list(reversed(kept)), "path": str(path), "exists": True}


@router.delete("/logs/retrieval", summary="清空检索日志")
async def clear_retrieval_logs(db: AsyncSession = Depends(get_db)) -> dict:
    """删除 retrieval_logs 表全部记录。"""
    result = await db.execute(delete(RetrievalLog))
    await db.commit()
    deleted = int(result.rowcount or 0)
    logger.info("清空检索日志: %s 条", deleted)
    return {"ok": True, "deleted": deleted}


@router.delete("/logs/system", summary="清空系统日志")
def clear_system_logs() -> dict:
    """清空 logs/app.log 内容（先释放 loguru 句柄再截断）。"""
    info = clear_log_file()
    logger.info("清空系统日志: 释放 %s 字节", info.get("bytes_cleared", 0))
    return {"ok": True, **info}
