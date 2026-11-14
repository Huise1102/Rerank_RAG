from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, require_admin
from src.db.models import StrategyPreset, User
from src.db.session import get_db
from src.schemas.knowledge import (
    StrategyCreateRequest,
    StrategyOut,
    StrategyUpdateRequest,
)

router = APIRouter(tags=["检索策略"])


@router.get("/strategies", response_model=list[StrategyOut])
async def list_strategies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    statement = select(StrategyPreset)
    if user.role != "admin":
        statement = statement.where(StrategyPreset.is_open.is_(True))
    result = await db.execute(statement.order_by(StrategyPreset.id))
    return result.scalars().all()


@router.post("/admin/strategies", response_model=StrategyOut)
async def create_strategy(
    payload: StrategyCreateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(StrategyPreset.id).where(StrategyPreset.name == payload.name))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="策略名称已存在")
    preset = StrategyPreset(**payload.model_dump())
    if preset.is_default:
        await db.execute(update(StrategyPreset).values(is_default=False))
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset


@router.patch("/admin/strategies/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: int,
    payload: StrategyUpdateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    preset = await db.get(StrategyPreset, strategy_id)
    if preset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="策略不存在")
    values = payload.model_dump(exclude_none=True)
    if "name" in values and values["name"] != preset.name:
        duplicated = await db.execute(
            select(StrategyPreset.id).where(
                StrategyPreset.name == values["name"], StrategyPreset.id != strategy_id
            )
        )
        if duplicated.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="策略名称已存在")
    if values.get("is_default"):
        await db.execute(
            update(StrategyPreset).where(StrategyPreset.id != strategy_id).values(is_default=False)
        )
    for key, value in values.items():
        setattr(preset, key, value)
    if not preset.use_vector and not preset.use_bm25:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="至少启用一种召回方式")
    await db.commit()
    await db.refresh(preset)
    return preset


@router.delete("/admin/strategies/{strategy_id}")
async def delete_strategy(
    strategy_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    preset = await db.get(StrategyPreset, strategy_id)
    if preset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if preset.is_default:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="默认策略不能删除")
    await db.delete(preset)
    await db.commit()
    return {"ok": True}
