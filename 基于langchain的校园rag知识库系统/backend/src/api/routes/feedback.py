from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user
from src.db.models import Feedback, User
from src.db.session import get_db
from src.schemas.feedback import FeedbackCreateRequest, FeedbackOut, MyFeedbackListItem
from src.services.feedback_service import create_feedback

router = APIRouter(prefix="/feedback", tags=["用户反馈"])


@router.post("", response_model=FeedbackOut)
async def submit_feedback(
    payload: FeedbackCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_feedback(db, user, payload)


@router.get("/mine", response_model=list[MyFeedbackListItem])
async def my_feedback(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Feedback).where(Feedback.user_id == user.id).order_by(Feedback.id.desc()).limit(100)
    )
    return result.scalars().all()


@router.get("/mine/{feedback_id}", response_model=FeedbackOut)
async def my_feedback_detail(
    feedback_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    feedback = await db.get(Feedback, feedback_id)
    if feedback is None or feedback.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="反馈不存在")
    return feedback
