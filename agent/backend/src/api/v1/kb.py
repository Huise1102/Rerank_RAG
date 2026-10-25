"""知识库管理路由（kb.py）。无用户体系，不涉及权限。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.session import get_db
from src.schemas.kb import KBCreate, KBOut, KBUpdate, StrategyPayload
from src.services import kb_service

logger = get_logger(__name__)

router = APIRouter(prefix="/kb", tags=["知识库"])


@router.get("", response_model=list[KBOut], summary="知识库列表")
async def list_kbs(db: AsyncSession = Depends(get_db)) -> list:
    return await kb_service.list_kbs(db)


@router.post("", response_model=KBOut, status_code=status.HTTP_201_CREATED, summary="新建知识库")
async def create_kb(payload: KBCreate, db: AsyncSession = Depends(get_db)):
    return await kb_service.create_kb(db, payload)


@router.get("/{kb_id}", response_model=KBOut, summary="知识库详情")
async def get_kb(kb_id: int, db: AsyncSession = Depends(get_db)):
    kb = await kb_service.get_kb(db, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    return kb


@router.put("/{kb_id}", response_model=KBOut, summary="更新知识库")
async def update_kb(kb_id: int, payload: KBUpdate, db: AsyncSession = Depends(get_db)):
    kb = await kb_service.update_kb(db, kb_id, payload)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    return kb


@router.patch("/{kb_id}/strategies", response_model=KBOut, summary="更新检索构建块开关")
async def update_strategies(
    kb_id: int, payload: StrategyPayload, db: AsyncSession = Depends(get_db)
):
    kb = await kb_service.update_strategies(db, kb_id, payload.to_dict())
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    return kb


@router.delete("/{kb_id}", summary="删除知识库（含索引与文档）")
async def delete_kb(kb_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    ok = await kb_service.delete_kb(db, kb_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    return {"deleted": True, "kb_id": kb_id}
