import asyncio
import re
from collections import deque

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
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


def _read_system_log(path, limit: int) -> list[dict]:
    """读取日志文件末尾内容，多行堆栈合并到上一条记录。"""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        raw = deque(handle, maxlen=limit * 20)

    entries: list[dict] = []
    for line in raw:
        text = line.rstrip("\n")
        match = LOG_PATTERN.match(text)
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
        elif entries and text.strip():
            entries[-1]["message"] = f"{entries[-1]['message']}\n{text.strip()}"
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
    lines: int = Query(default=200, ge=20, le=1000),
    level: str | None = None,
    keyword: str | None = None,
    admin: User = Depends(require_admin),
):
    path = settings.logs_path / "backend.log"
    if not path.exists():
        return {"items": [], "total": 0, "path": str(path)}

    entries = await asyncio.to_thread(_read_system_log, path, lines)
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
    return {"items": entries, "total": len(entries), "path": str(path)}
