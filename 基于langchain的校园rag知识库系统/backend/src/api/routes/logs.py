import asyncio
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_admin
from src.core.config import settings
from src.db.models import AuditLog, RetrievalLog, User
from src.db.session import get_db

router = APIRouter(prefix="/admin/logs", tags=["日志"])

LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\w+)\s+([\w\.]+)\s+(?:\[([\w\-]*)\]\s+)?(.*)$"
)
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ClearLogsRequest(BaseModel):
    retrieval: bool = False
    audit: bool = False
    system: bool = False


def _clear_system_log(path: Path) -> int:
    """清空系统日志文件及轮转备份，返回清理的字节数。"""
    if not path.exists():
        return 0
    size = path.stat().st_size
    target = str(path.resolve())
    for handler in logging.getLogger().handlers:
        if not isinstance(handler, RotatingFileHandler):
            continue
        if str(Path(handler.baseFilename).resolve()) != target or handler.stream is None:
            continue
        handler.acquire()
        try:
            handler.flush()
            handler.stream.seek(0)
            handler.stream.truncate(0)
        finally:
            handler.release()
    for backup in path.parent.glob(f"{path.name}.*"):
        if backup.is_file():
            backup.unlink(missing_ok=True)
    return size


def _read_system_log(path: Path, max_bytes: int = 2 * 1024 * 1024) -> list[dict]:
    """读取日志文件尾部内容，避免大文件全量扫描，多行堆栈合并到上一条记录。"""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        text = handle.read().decode("utf-8", errors="replace")
    if size > max_bytes:
        text = text.split("\n", 1)[-1]

    entries: list[dict] = []
    for line in text.splitlines():
        match = LOG_PATTERN.match(line)
        if match:
            entries.append(
                {
                    "time": match.group(1),
                    "level": match.group(2),
                    "logger": match.group(3),
                    "request_id": match.group(4) or "",
                    "message": match.group(5),
                }
            )
        elif entries and line.strip():
            entries[-1]["message"] = f"{entries[-1]['message']}\n{line.strip()}"
    return entries


@router.get("/retrieval")
async def list_retrieval_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    kb_id: int | None = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    statement = select(RetrievalLog)
    if keyword:
        statement = statement.where(RetrievalLog.question.like(f"%{keyword.strip()}%"))
    if kb_id:
        statement = statement.where(RetrievalLog.kb_id == kb_id)
    total = int(
        (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )
    result = await db.execute(
        statement.order_by(RetrievalLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = result.scalars().all()
    return {
        "items": [
            {
                "id": item.id,
                "user_id": item.user_id,
                "kb_id": item.kb_id,
                "conversation_id": item.conversation_id,
                "strategy_name": item.strategy_name,
                "question": item.question,
                "vector_hits": item.vector_hits,
                "bm25_hits": item.bm25_hits,
                "fused_hits": item.fused_hits,
                "reranked_hits": item.reranked_hits,
                "duration_ms": item.duration_ms,
                "citations": item.citations,
                "created_at": item.created_at,
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/audit")
async def list_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    action: str | None = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    statement = select(AuditLog)
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(or_(AuditLog.username.like(like), AuditLog.detail.like(like)))
    if action:
        statement = statement.where(AuditLog.action == action)
    total = int(
        (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )
    result = await db.execute(
        statement.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = result.scalars().all()
    return {
        "items": [
            {
                "id": item.id,
                "user_id": item.user_id,
                "username": item.username,
                "action": item.action,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "detail": item.detail,
                "ip": item.ip,
                "created_at": item.created_at,
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/system")
async def list_system_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    level: str | None = None,
    keyword: str | None = None,
    admin: User = Depends(require_admin),
):
    path = settings.logs_path / "backend.log"
    if not path.exists():
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "path": str(path)}

    entries = await asyncio.to_thread(_read_system_log, path)
    if level:
        target = level.upper()
        entries = [item for item in entries if item["level"] == target]
    if keyword:
        needle = keyword.strip()
        entries = [
            item
            for item in entries
            if needle in item["message"] or needle in item["logger"]
        ]
    entries.reverse()
    total = len(entries)
    start = (page - 1) * page_size
    return {
        "items": entries[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "path": str(path),
    }


@router.post("/clear")
async def clear_logs(
    payload: ClearLogsRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not (payload.retrieval or payload.audit or payload.system):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请选择要清理的日志")

    cleared: dict[str, int] = {}
    scopes: list[str] = []
    if payload.retrieval:
        result = await db.execute(delete(RetrievalLog))
        cleared["retrieval"] = max(result.rowcount or 0, 0)
        scopes.append("检索日志")
    if payload.audit:
        result = await db.execute(delete(AuditLog))
        cleared["audit"] = max(result.rowcount or 0, 0)
        scopes.append("审计日志")
    if payload.system:
        cleared["system_bytes"] = await asyncio.to_thread(
            _clear_system_log, settings.logs_path / "backend.log"
        )
        scopes.append("系统日志文件")
    await db.commit()

    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="clear_logs",
            target_type="log",
            detail="清理日志：" + "、".join(scopes),
        )
    )
    await db.commit()
    return {"cleared": cleared}

