from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import (
    Conversation,
    Feedback,
    FeedbackEvent,
    KnowledgeBase,
    Message,
    StrategyPreset,
    User,
    utcnow,
)
from src.schemas.feedback import FEEDBACK_TYPES, FeedbackCreateRequest
from src.utils.ratelimit import increment_counter
from src.utils.sanitize import sanitize_text

STATUS_LABELS = {
    "pending": "待处理",
    "processing": "处理中",
    "resolved": "已解决",
    "rejected": "已驳回",
}


async def create_feedback(
    db: AsyncSession, user: User, payload: FeedbackCreateRequest
) -> Feedback:
    count = await increment_counter(f"feedback:{user.id}", 3600)
    if count > settings.feedback_hourly_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="提交过于频繁，请稍后再试")
    if payload.feedback_type not in FEEDBACK_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="反馈类型不合法")

    conversation_id = payload.conversation_id
    question_snapshot = ""
    answer_snapshot = ""
    citations_snapshot = None
    kb_id = None
    strategy_name = None

    if payload.assistant_message_id is not None:
        message = await db.get(Message, payload.assistant_message_id)
        if message is None or message.role != "assistant":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="消息不存在")
        conversation = await db.get(Conversation, message.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="消息不存在")

        duplicated = await db.execute(
            select(Feedback.id).where(
                Feedback.user_id == user.id,
                Feedback.assistant_message_id == message.id,
                Feedback.status != "rejected",
            )
        )
        if duplicated.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="该回答已反馈，请勿重复提交")

        question_result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.role == "user",
                Message.id < message.id,
            )
            .order_by(Message.id.desc())
            .limit(1)
        )
        question = question_result.scalar_one_or_none()
        question_snapshot = question.content if question else ""
        if question is not None:
            conversation_id = message.conversation_id
        answer_snapshot = message.content
        citations_snapshot = message.citations
        kb_id = message.kb_id
        if message.strategy_id:
            preset = await db.get(StrategyPreset, message.strategy_id)
            strategy_name = preset.name if preset else None
    elif conversation_id is not None:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
        kb_id = conversation.kb_id

    if kb_id is not None:
        kb = await db.get(KnowledgeBase, kb_id)
        if kb is None:
            kb_id = None

    feedback = Feedback(
        ticket_no="pending",
        user_id=user.id,
        conversation_id=conversation_id,
        assistant_message_id=payload.assistant_message_id,
        kb_id=kb_id,
        strategy=strategy_name,
        question_snapshot=sanitize_text(question_snapshot, 2000),
        answer_snapshot=sanitize_text(answer_snapshot, 8000),
        citations_snapshot=citations_snapshot,
        feedback_type=payload.feedback_type,
        description=sanitize_text(payload.description, 1000),
        expected_answer=sanitize_text(payload.expected_answer, 1000),
        contact=sanitize_text(payload.contact, 100),
        status="pending",
    )
    db.add(feedback)
    await db.flush()
    feedback.ticket_no = f"FB-{datetime.now().strftime('%Y%m%d')}-{feedback.id:04d}"
    db.add(
        FeedbackEvent(
            feedback_id=feedback.id,
            operator_id=user.id,
            operator_name=user.display_name or user.username,
            from_status=None,
            to_status="pending",
            note="用户提交反馈",
        )
    )
    await db.commit()
    await db.refresh(feedback)
    return feedback


async def update_feedback_status(
    db: AsyncSession,
    admin: User,
    feedback: Feedback,
    action: str,
    reply: str | None,
    reject_reason: str | None,
    note: str | None,
) -> Feedback:
    previous = feedback.status
    target_status = previous

    if action == "accept":
        if previous != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="仅待处理反馈可以受理")
        target_status = "processing"
        feedback.handler_id = admin.id
    elif action == "resolve":
        if previous not in {"pending", "processing"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="当前状态不能直接解决")
        if not reply and not note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写处理说明或回复")
        target_status = "resolved"
        feedback.handler_id = admin.id
        feedback.resolved_at = utcnow()
    elif action == "reply_resolve":
        if previous not in {"pending", "processing"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="当前状态不能回复并解决")
        if not reply:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写用户可见回复")
        target_status = "resolved"
        feedback.handler_id = admin.id
        feedback.resolved_at = utcnow()
    elif action == "reject":
        if previous not in {"pending", "processing"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="当前状态不能驳回")
        if not reject_reason:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="驳回必须填写原因")
        target_status = "rejected"
        feedback.handler_id = admin.id
        feedback.reject_reason = sanitize_text(reject_reason, 1000)
        feedback.resolved_at = utcnow()
    elif action == "reopen":
        if previous not in {"resolved", "rejected"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="仅已办结反馈可以重新打开")
        target_status = "processing"
        feedback.handler_id = admin.id
        feedback.resolved_at = None
    elif action == "note":
        if not note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写内部备注")
        target_status = previous
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的处理动作")

    if reply is not None:
        feedback.reply = sanitize_text(reply, 2000)
    if note is not None:
        feedback.handle_note = sanitize_text(note, 1000)
    feedback.status = target_status
    feedback.updated_at = utcnow()
    db.add(
        FeedbackEvent(
            feedback_id=feedback.id,
            operator_id=admin.id,
            operator_name=admin.display_name or admin.username,
            from_status=previous,
            to_status=target_status,
            note=sanitize_text(note or reply or reject_reason, 1000) or STATUS_LABELS.get(target_status),
        )
    )
    await db.commit()
    await db.refresh(feedback)
    return feedback


async def feedback_stats(db: AsyncSession) -> dict:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    async def count(condition) -> int:
        return int((await db.execute(select(func.count(Feedback.id)).where(condition))).scalar_one())

    today_resolved = int(
        (
            await db.execute(
                select(func.count(Feedback.id)).where(
                    Feedback.resolved_at.is_not(None), Feedback.resolved_at >= today_start
                )
            )
        ).scalar_one()
    )
    total = int((await db.execute(select(func.count(Feedback.id)))).scalar_one())
    return {
        "pending": await count(Feedback.status == "pending"),
        "processing": await count(Feedback.status == "processing"),
        "today_new": await count(Feedback.created_at >= today_start),
        "today_resolved": today_resolved,
        "total": total,
    }
