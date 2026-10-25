"""FastAPI 依赖注入（deps.py）。无认证：只提供数据库会话与"取知识库或 404"辅助。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.knowledge_base import KnowledgeBase
from src.db.session import get_db

__all__ = ["get_db", "get_kb_or_404"]


async def get_kb_or_404(
    kb_id: int,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBase:
    """取知识库，不存在则 404。"""
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"知识库 {kb_id} 不存在")
    return kb
