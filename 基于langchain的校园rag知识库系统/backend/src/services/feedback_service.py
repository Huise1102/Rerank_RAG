from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import (
    Conversation,
    Department,
    Feedback,
    FeedbackEvent,
    KnowledgeBase,
    Message,
    StrategyPreset,
    User,
    utcnow,
)
from src.schemas.feedback import FEEDBACK_TYPES, FeedbackCreateRequest, TransferCreateRequest
from src.services.notification_service import notify_feedback_status, notify_transfer_created
from src.utils.ratelimit import increment_counter
from src.utils.sanitize import sanitize_text

STATUS_LABELS = {
    "pending": "待处理",
    "processing": "处理中",
    "replied": "已回复",
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


async def create_transfer(
    db: AsyncSession, user: User, payload: TransferCreateRequest
) -> Feedback:
    count = await increment_counter(f"feedback:{user.id}", 3600)
    if count > settings.feedback_hourly_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="提交过于频繁，请稍后再试")

    question_snapshot = payload.question or ""
    answer_snapshot = ""
    citations_snapshot = None
    conversation_id = payload.conversation_id
    kb_id = None
    strategy_name = None
    assistant_message_id = payload.assistant_message_id

    if assistant_message_id is not None:
        message = await db.get(Message, assistant_message_id)
        if message is None or message.role != "assistant":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="消息不存在")
        conversation = await db.get(Conversation, message.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="消息不存在")
        duplicated = await db.execute(
            select(Feedback.id).where(
                Feedback.user_id == user.id,
                Feedback.assistant_message_id == message.id,
                Feedback.kind == "transfer",
                Feedback.status != "rejected",
            )
        )
        if duplicated.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="该回答已转办，请勿重复提交")
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
        question_snapshot = question.content if question else question_snapshot
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

    if not question_snapshot.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请提供需要转办的问题")

    assigned_department_id = None
    if kb_id is not None:
        kb = await db.get(KnowledgeBase, kb_id)
        if kb is None:
            kb_id = None
        elif kb.department_id is not None:
            assigned_department_id = kb.department_id
    if assigned_department_id is None:
        department = (
            await db.execute(
                select(Department).where(Department.code == settings.default_department_code)
            )
        ).scalar_one_or_none()
        if department is not None:
            assigned_department_id = department.id

    feedback = Feedback(
        ticket_no="pending",
        user_id=user.id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        kb_id=kb_id,
        strategy=strategy_name,
        kind="transfer",
        assigned_department_id=assigned_department_id,
        transfer_reason=sanitize_text(payload.reason or "用户申请转人工", 1000),
        question_snapshot=sanitize_text(question_snapshot, 2000),
        answer_snapshot=sanitize_text(answer_snapshot, 8000),
        citations_snapshot=citations_snapshot,
        feedback_type="转人工",
        description=sanitize_text(payload.reason or "用户申请转人工处理", 1000),
        contact=sanitize_text(payload.contact, 100),
        status="pending",
    )
    db.add(feedback)
    await db.flush()
    feedback.ticket_no = f"TR-{datetime.now().strftime('%Y%m%d')}-{feedback.id:04d}"
    db.add(
        FeedbackEvent(
            feedback_id=feedback.id,
            operator_id=user.id,
            operator_name=user.display_name or user.username,
            from_status=None,
            to_status="pending",
            note="用户发起转人工",
        )
    )
    await notify_transfer_created(db, feedback)
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
    assigned_department_id: int | None = None,
) -> Feedback:
    previous = feedback.status
    target_status = previous

    if action == "accept":
        if previous != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="仅待处理反馈可以受理")
        target_status = "processing"
        feedback.handler_id = admin.id
    elif action == "reply":
        if previous not in {"pending", "processing", "replied"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="当前状态不能回复")
        if not reply:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写用户可见回复")
        target_status = "replied"
        feedback.handler_id = admin.id
    elif action == "resolve":
        if previous not in {"pending", "processing", "replied"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="当前状态不能直接解决")
        if not reply and not note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写处理说明或回复")
        target_status = "resolved"
        feedback.handler_id = admin.id
        feedback.resolved_at = utcnow()
    elif action == "reply_resolve":
        if previous not in {"pending", "processing", "replied"}:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="当前状态不能回复并解决")
        if not reply:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写用户可见回复")
        target_status = "resolved"
        feedback.handler_id = admin.id
        feedback.resolved_at = utcnow()
    elif action == "reject":
        if previous not in {"pending", "processing", "replied"}:
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
    elif action == "forward":
        if assigned_department_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请选择转办部门")
        department = await db.get(Department, assigned_department_id)
        if department is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="转办部门不存在")
        feedback.assigned_department_id = department.id
        target_status = "processing"
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
    if target_status in {"processing", "replied"} and feedback.first_response_at is None:
        feedback.first_response_at = utcnow()
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
    await notify_feedback_status(db, feedback, action)
    await db.commit()
    await db.refresh(feedback)
    return feedback


async def feedback_stats(db: AsyncSession, department_id: int | None = None) -> dict:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    scope_condition = (
        Feedback.assigned_department_id == department_id if department_id is not None else None
    )

    async def count(condition) -> int:
        statement = select(func.count(Feedback.id)).where(condition)
        if scope_condition is not None:
            statement = statement.where(scope_condition)
        return int((await db.execute(statement)).scalar_one())

    today_resolved = await count(
        (Feedback.resolved_at.is_not(None)) & (Feedback.resolved_at >= today_start)
    )
    total_statement = select(func.count(Feedback.id))
    if scope_condition is not None:
        total_statement = total_statement.where(scope_condition)
    total = int((await db.execute(total_statement)).scalar_one())

    open_statement = select(Feedback).where(
        Feedback.status.in_(["pending", "processing", "replied"])
    )
    if scope_condition is not None:
        open_statement = open_statement.where(scope_condition)
    open_items = (await db.execute(open_statement)).scalars().all()
    now = datetime.now()
    first_deadline = timedelta(hours=settings.transfer_first_response_hours)
    resolve_deadline = timedelta(hours=settings.transfer_resolve_hours)
    overdue = 0
    for item in open_items:
        if item.kind != "transfer":
            continue
        if item.status == "pending" and item.created_at + first_deadline < now:
            overdue += 1
        elif item.status in {"processing", "replied"} and item.created_at + resolve_deadline < now:
            overdue += 1

    return {
        "pending": await count(Feedback.status == "pending"),
        "processing": await count(Feedback.status == "processing"),
        "replied": await count(Feedback.status == "replied"),
        "today_new": await count(Feedback.created_at >= today_start),
        "today_resolved": today_resolved,
        "total": total,
        "transfer_pending": await count((Feedback.kind == "transfer") & (Feedback.status == "pending")),
        "overdue": overdue,
    }
