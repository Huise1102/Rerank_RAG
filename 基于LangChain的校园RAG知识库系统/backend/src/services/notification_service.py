from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Feedback, Notification, User, utcnow

STATUS_LABELS = {
    "pending": "待处理",
    "processing": "处理中",
    "replied": "已回复",
    "resolved": "已解决",
    "rejected": "已驳回",
}


def _add(
    db: AsyncSession,
    user_id: int,
    kind: str,
    title: str,
    content: str,
    link: str | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        channel="in_app",
        type=kind,
        title=title[:200],
        content=content[:500],
        link=link,
        status="sent",
        sent_at=utcnow(),
    )
    db.add(notification)
    return notification


async def notify_department_admins(
    db: AsyncSession,
    department_id: int | None,
    kind: str,
    title: str,
    content: str,
    link: str | None = None,
) -> int:
    """给部门管理员发送站内通知，部门缺少管理员时回落到系统管理员。"""
    if department_id is None:
        admin_rows = await db.execute(
            select(User.id).where(User.role == "admin", User.status == "active")
        )
        recipients = list(admin_rows.scalars().all())
    else:
        statement = select(User.id).where(
            User.role == "dept_admin",
            User.status == "active",
            User.department_id == department_id,
        )
        recipients = list((await db.execute(statement)).scalars().all())
        if not recipients:
            admin_rows = await db.execute(
                select(User.id).where(User.role == "admin", User.status == "active")
            )
            recipients = list(admin_rows.scalars().all())
    for user_id in recipients:
        _add(db, user_id, kind, title, content, link)
    return len(recipients)


async def notify_user(
    db: AsyncSession,
    user_id: int,
    kind: str,
    title: str,
    content: str,
    link: str | None = None,
) -> Notification:
    return _add(db, user_id, kind, title, content, link)


async def notify_transfer_created(db: AsyncSession, feedback: Feedback) -> None:
    await notify_department_admins(
        db,
        feedback.assigned_department_id,
        "transfer",
        "新的转办待处理",
        f"{feedback.ticket_no}：{feedback.question_snapshot[:80]}",
        "/feedback",
    )


async def notify_feedback_status(db: AsyncSession, feedback: Feedback, action: str) -> None:
    label = STATUS_LABELS.get(feedback.status, feedback.status)
    title = "转办状态更新" if feedback.kind == "transfer" else "反馈状态更新"
    content = f"{feedback.ticket_no} 已更新为{label}"
    if feedback.reply:
        content = f"{content}：{feedback.reply[:120]}"
    elif feedback.reject_reason:
        content = f"{content}：{feedback.reject_reason[:120]}"
    _add(db, feedback.user_id, "feedback", title, content, "/feedback")
