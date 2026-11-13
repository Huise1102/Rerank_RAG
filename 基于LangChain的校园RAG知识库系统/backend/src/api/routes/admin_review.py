from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, require_document_manager
from src.db.models import AuditLog, Document, DocumentVersion, KnowledgeBase, Notification, User, utcnow
from src.db.session import get_db
from src.services.dashboard_service import review_next_date
from src.services.document_service import repeal_version
from src.services.notification_service import notify_department_admins, notify_user

router = APIRouter(prefix="/admin/review", tags=["文档复核"])


class ReviewActionRequest(BaseModel):
    action: str = Field(pattern="^(confirm|revise|repeal)$")
    note: str = Field(default="", max_length=500)


def _audit(db: AsyncSession, user: User, action: str, document: Document, detail: str, ip: str) -> None:
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=action,
            target_type="document",
            target_id=str(document.id),
            detail=detail,
            ip=ip,
        )
    )


@router.get("/due")
async def list_review_due(
    days: int = Query(default=30, ge=0, le=365),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    deadline = date.today() + timedelta(days=days)
    statement = (
        select(Document, KnowledgeBase.name)
        .outerjoin(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
        .where(
            Document.doc_status == "active",
            Document.review_due_date.is_not(None),
            Document.review_due_date <= deadline,
        )
        .order_by(Document.review_due_date)
    )
    if user.role == "dept_admin":
        statement = statement.where(Document.department_id == user.department_id)
    rows = (await db.execute(statement)).all()
    return {
        "items": [
            {
                "id": document.id,
                "title": document.title,
                "filename": document.filename,
                "kb_id": document.kb_id,
                "kb_name": kb_name,
                "department_id": document.department_id,
                "owner_user_id": document.owner_user_id,
                "review_due_date": document.review_due_date,
                "overdue": document.review_due_date < date.today(),
            }
            for document, kb_name in rows
        ]
    }


@router.post("/{document_id}/action")
async def review_document(
    document_id: int,
    payload: ReviewActionRequest,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    if user.role == "dept_admin" and document.department_id != user.department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能复核本部门文档")
    ip = client_ip(request)
    kb = await db.get(KnowledgeBase, document.kb_id)

    if payload.action == "repeal":
        version = (
            await db.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        if version is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="文档没有可废止的已发布版本")
        await repeal_version(db, user, version, payload.note or "到期复核废止", ip)
        await notify_department_admins(
            db,
            document.department_id,
            "review",
            "文档已到期废止",
            f"《{document.title}》已废止，原因：{payload.note or '到期复核废止'}",
            "/knowledge",
        )
        if document.owner_user_id is not None:
            await notify_user(
                db,
                document.owner_user_id,
                "review",
                "你负责的文档已废止",
                f"《{document.title}》已废止，原因：{payload.note or '到期复核废止'}",
                "/knowledge",
            )
        await db.commit()
        return {"id": document.id, "action": "repeal", "status": "repealed"}

    if payload.action == "confirm":
        document.review_due_date = review_next_date(kb.review_cycle_days if kb else 180)
        detail = f"确认继续有效，下次复核日期 {document.review_due_date.isoformat()}"
    else:
        document.review_due_date = date.today() + timedelta(days=30)
        detail = f"发起修订，30 天内处理，提醒日期 {document.review_due_date.isoformat()}"
    document.updated_at = utcnow()
    _audit(db, user, f"review_{payload.action}", document, detail, ip)
    await notify_department_admins(
        db,
        document.department_id,
        "review",
        "文档复核状态更新",
        f"《{document.title}》{detail}",
        "/knowledge",
    )
    if document.owner_user_id is not None:
        await notify_user(
            db,
            document.owner_user_id,
            "review",
            "你负责的文档已更新复核状态",
            f"《{document.title}》{detail}",
            "/knowledge",
        )
    await db.commit()
    return {
        "id": document.id,
        "action": payload.action,
        "review_due_date": document.review_due_date,
    }


@router.post("/notify")
async def notify_review_due(
    days: int = Query(default=30, ge=1, le=90),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    deadline = date.today() + timedelta(days=days)
    statement = (
        select(Document)
        .where(
            Document.doc_status == "active",
            Document.review_due_date.is_not(None),
            Document.review_due_date <= deadline,
        )
        .order_by(Document.review_due_date)
    )
    if user.role == "dept_admin":
        statement = statement.where(Document.department_id == user.department_id)
    documents = (await db.execute(statement)).scalars().all()
    since = datetime.utcnow() - timedelta(days=7)
    created = 0
    for document in documents:
        link = f"/knowledge?doc={document.id}"
        exists = (
            await db.execute(
                select(Notification.id)
                .where(
                    Notification.type == "review",
                    Notification.link == link,
                    Notification.created_at >= since,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        created += await notify_department_admins(
            db,
            document.department_id,
            "review",
            "文档到期复核提醒",
            f"《{document.title}》复核日期为 {document.review_due_date.isoformat()}",
            link,
        )
    await db.commit()
    return {"documents": len(documents), "notifications": created}
