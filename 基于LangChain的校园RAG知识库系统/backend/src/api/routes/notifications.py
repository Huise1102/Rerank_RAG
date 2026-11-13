from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user
from src.db.models import Notification, User, utcnow
from src.db.session import get_db

router = APIRouter(prefix="/notifications", tags=["站内通知"])


def _serialize(item: Notification) -> dict:
    return {
        "id": item.id,
        "type": item.type,
        "title": item.title,
        "content": item.content,
        "link": item.link,
        "read": item.read_at is not None,
        "created_at": item.created_at,
        "read_at": item.read_at,
    }


@router.get("")
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    statement = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    items = (
        (await db.execute(statement.order_by(Notification.id.desc()).limit(limit))).scalars().all()
    )
    unread = int(
        (
            await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user.id, Notification.read_at.is_(None)
                )
            )
        ).scalar_one()
    )
    return {"items": [_serialize(item) for item in items], "unread": unread}


@router.patch("/read-all")
async def read_all_notifications(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items = (
        (
            await db.execute(
                select(Notification).where(
                    Notification.user_id == user.id, Notification.read_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    now: datetime = utcnow()
    for item in items:
        item.read_at = now
    await db.commit()
    return {"updated": len(items)}


@router.patch("/{notification_id}")
async def read_notification(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="通知不存在")
    if notification.read_at is None:
        notification.read_at = utcnow()
        await db.commit()
    return _serialize(notification)
