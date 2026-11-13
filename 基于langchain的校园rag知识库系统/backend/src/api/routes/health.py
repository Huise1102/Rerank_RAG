from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db

router = APIRouter(tags=["健康检查"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    database = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    status = "ok" if database == "ok" else "degraded"
    return {"status": status, "database": database, "service": "campus-rag-backend"}
