from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, require_document_manager
from src.db.models import AuditLog, Feedback, FeedbackEvent, KnowledgeBase, User
from src.db.session import get_db
from src.schemas.feedback import FeedbackStatsResponse, FeedbackUpdateRequest
from src.services.feedback_service import feedback_stats, update_feedback_status

router = APIRouter(prefix="/admin/feedback", tags=["反馈处理"])


def _serialize(feedback: Feedback, submitter: User | None, kb_name: str | None, events: list[FeedbackEvent] | None = None) -> dict:
    data = {
        "id": feedback.id,
        "ticket_no": feedback.ticket_no,
        "kind": feedback.kind,
        "status": feedback.status,
        "feedback_type": feedback.feedback_type,
        "assigned_department_id": feedback.assigned_department_id,
        "assigned_user_id": feedback.assigned_user_id,
        "transfer_reason": feedback.transfer_reason,
        "first_response_at": feedback.first_response_at,
        "description": feedback.description,
        "expected_answer": feedback.expected_answer,
        "contact": feedback.contact,
        "question_snapshot": feedback.question_snapshot,
        "answer_snapshot": feedback.answer_snapshot,
        "citations_snapshot": feedback.citations_snapshot,
        "kb_id": feedback.kb_id,
        "kb_name": kb_name,
        "strategy": feedback.strategy,
        "conversation_id": feedback.conversation_id,
        "assistant_message_id": feedback.assistant_message_id,
        "handler_id": feedback.handler_id,
        "handle_note": feedback.handle_note,
        "reply": feedback.reply,
        "reject_reason": feedback.reject_reason,
        "created_at": feedback.created_at,
        "updated_at": feedback.updated_at,
        "resolved_at": feedback.resolved_at,
        "submitter": {
            "id": submitter.id if submitter else None,
            "username": submitter.username if submitter else "",
            "display_name": submitter.display_name if submitter else "",
        },
    }
    if events is not None:
        data["events"] = [
            {
                "id": event.id,
                "operator_name": event.operator_name,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "note": event.note,
                "created_at": event.created_at,
            }
            for event in events
        ]
    return data


def _department_scope(user: User) -> int | None:
    return None if user.role == "admin" else user.department_id


def _ensure_feedback_scope(user: User, feedback: Feedback) -> None:
    if user.role == "admin":
        return
    if feedback.assigned_department_id != user.department_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="反馈不存在")


@router.get("/stats", response_model=FeedbackStatsResponse)
async def stats(
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    return await feedback_stats(db, _department_scope(user))


@router.get("")
async def list_feedback(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_value: str | None = Query(default=None, alias="status"),
    feedback_type: str | None = None,
    kb_id: int | None = None,
    submitter: str | None = None,
    keyword: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    kind: str | None = None,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    statement = select(Feedback, User, KnowledgeBase).join(
        User, User.id == Feedback.user_id, isouter=True
    ).join(KnowledgeBase, KnowledgeBase.id == Feedback.kb_id, isouter=True)
    department_id = _department_scope(user)
    if department_id is not None:
        statement = statement.where(Feedback.assigned_department_id == department_id)
    if kind:
        statement = statement.where(Feedback.kind == kind)
    if status_value:
        statement = statement.where(Feedback.status == status_value)
    if feedback_type:
        statement = statement.where(Feedback.feedback_type == feedback_type)
    if kb_id:
        statement = statement.where(Feedback.kb_id == kb_id)
    if submitter:
        like = f"%{submitter.strip()}%"
        statement = statement.where(or_(User.username.like(like), User.display_name.like(like)))
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(
            or_(
                Feedback.ticket_no.like(like),
                Feedback.question_snapshot.like(like),
                Feedback.description.like(like),
                Feedback.answer_snapshot.like(like),
            )
        )
    if start:
        statement = statement.where(Feedback.created_at >= start)
    if end:
        statement = statement.where(Feedback.created_at <= end)

    total = int(
        (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )
    result = await db.execute(
        statement.order_by(Feedback.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = [
        _serialize(feedback, user, kb.name if kb else None)
        for feedback, user, kb in result.all()
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{feedback_id}")
async def feedback_detail(
    feedback_id: int,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    feedback = await db.get(Feedback, feedback_id)
    if feedback is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="反馈不存在")
    _ensure_feedback_scope(user, feedback)
    submitter = await db.get(User, feedback.user_id)
    kb = await db.get(KnowledgeBase, feedback.kb_id) if feedback.kb_id else None
    events = (
        (
            await db.execute(
                select(FeedbackEvent)
                .where(FeedbackEvent.feedback_id == feedback_id)
                .order_by(FeedbackEvent.id)
            )
        )
        .scalars()
        .all()
    )
    return _serialize(feedback, submitter, kb.name if kb else None, list(events))


@router.patch("/{feedback_id}")
async def handle_feedback(
    feedback_id: int,
    payload: FeedbackUpdateRequest,
    request: Request,
    admin: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    feedback = await db.get(Feedback, feedback_id)
    if feedback is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="反馈不存在")
    _ensure_feedback_scope(admin, feedback)
    if (
        admin.role == "dept_admin"
        and payload.assigned_department_id is not None
        and payload.assigned_department_id != admin.department_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="不能转办到其他部门")
    updated = await update_feedback_status(
        db,
        admin,
        feedback,
        payload.action,
        payload.reply,
        payload.reject_reason,
        payload.note,
        payload.assigned_department_id,
    )
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="handle_feedback",
            target_type="feedback",
            target_id=str(feedback_id),
            detail=f"{payload.action}，状态 {updated.status}",
            ip=client_ip(request),
        )
    )
    await db.commit()
    submitter = await db.get(User, updated.user_id)
    kb = await db.get(KnowledgeBase, updated.kb_id) if updated.kb_id else None
    return _serialize(updated, submitter, kb.name if kb else None)
