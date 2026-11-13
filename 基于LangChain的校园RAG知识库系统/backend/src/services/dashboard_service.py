from datetime import date, datetime, timedelta

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Department,
    Document,
    Feedback,
    KnowledgeGap,
    RetrievalLog,
    User,
)

NEGATIVE_TYPES = ("答案错误", "引用错误或缺失", "回答不完整", "知识库无相关内容", "内容过期")


def dashboard_scope(user: User, department_id: int | None) -> int | None:
    if user.role == "admin":
        return department_id
    return user.department_id


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(len(ordered) * ratio), len(ordered) - 1)
    return int(ordered[index])


async def dashboard_summary(
    db: AsyncSession, days: int, department_id: int | None
) -> dict:
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    log_filter = [RetrievalLog.created_at >= since]
    if department_id is not None:
        log_filter.append(RetrievalLog.department_id == department_id)
    totals = (
        await db.execute(
            select(
                func.count(RetrievalLog.id),
                func.sum(case((RetrievalLog.reranked_hits == 0, 1), else_=0)),
                func.sum(case((RetrievalLog.cache_hit.is_(True), 1), else_=0)),
                func.count(distinct(RetrievalLog.user_id)),
            ).where(*log_filter)
        )
    ).one()
    questions = int(totals[0] or 0)
    zero_citation = int(totals[1] or 0)
    cache_hits = int(totals[2] or 0)
    active_users = int(totals[3] or 0)

    durations = list(
        (
            await db.execute(select(RetrievalLog.duration_ms).where(*log_filter))
        ).scalars().all()
    )
    durations = [int(item or 0) for item in durations]

    feedback_filter = [Feedback.created_at >= since]
    if department_id is not None:
        feedback_filter.append(Feedback.assigned_department_id == department_id)
    feedback_rows = (
        await db.execute(
            select(
                func.count(Feedback.id),
                func.sum(case((Feedback.kind == "transfer", 1), else_=0)),
                func.sum(
                    case(
                        (
                            (Feedback.kind == "feedback")
                            & (Feedback.feedback_type.in_(NEGATIVE_TYPES)),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(*feedback_filter)
        )
    ).one()
    feedback_total = int(feedback_rows[0] or 0)
    transfers = int(feedback_rows[1] or 0)
    negative = int(feedback_rows[2] or 0)

    pending_filter = [Feedback.status.in_(["pending", "processing", "replied"])]
    if department_id is not None:
        pending_filter.append(Feedback.assigned_department_id == department_id)
    pending_feedback = int(
        (await db.execute(select(func.count(Feedback.id)).where(*pending_filter))).scalar_one()
    )

    document_filter = [Document.doc_status == "active", Document.review_due_date.is_not(None)]
    if department_id is not None:
        document_filter.append(Document.department_id == department_id)
    stale_docs = int(
        (
            await db.execute(
                select(func.count(Document.id)).where(
                    *document_filter, Document.review_due_date <= datetime.utcnow().date()
                )
            )
        ).scalar_one()
    )

    gap_filter = [KnowledgeGap.status == "open"]
    if department_id is not None:
        gap_filter.append(KnowledgeGap.department_id == department_id)
    open_gaps = int(
        (await db.execute(select(func.count(KnowledgeGap.id)).where(*gap_filter))).scalar_one()
    )

    return {
        "days": days,
        "questions": questions,
        "active_users": active_users,
        "zero_citation": zero_citation,
        "zero_citation_rate": round(zero_citation / questions, 4) if questions else 0,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / questions, 4) if questions else 0,
        "feedback_total": feedback_total,
        "feedback_rate": round(feedback_total / questions, 4) if questions else 0,
        "negative_feedback": negative,
        "transfers": transfers,
        "transfer_rate": round(transfers / questions, 4) if questions else 0,
        "pending_feedback": pending_feedback,
        "stale_documents": stale_docs,
        "open_gaps": open_gaps,
        "p50_ms": _percentile(durations, 0.5),
        "p95_ms": _percentile(durations, 0.95),
    }


async def dashboard_trends(
    db: AsyncSession, days: int, department_id: int | None
) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    statement = (
        select(
            func.date(RetrievalLog.created_at),
            func.count(RetrievalLog.id),
            func.sum(case((RetrievalLog.reranked_hits == 0, 1), else_=0)),
            func.sum(case((RetrievalLog.cache_hit.is_(True), 1), else_=0)),
        )
        .where(RetrievalLog.created_at >= since)
        .group_by(func.date(RetrievalLog.created_at))
        .order_by(func.date(RetrievalLog.created_at))
    )
    if department_id is not None:
        statement = statement.where(RetrievalLog.department_id == department_id)
    rows = (await db.execute(statement)).all()
    return [
        {
            "date": str(row[0]),
            "questions": int(row[1] or 0),
            "zero_citation": int(row[2] or 0),
            "cache_hits": int(row[3] or 0),
        }
        for row in rows
    ]


async def department_comparison(
    db: AsyncSession, days: int, department_id: int | None
) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    statement = (
        select(
            RetrievalLog.department_id,
            Department.name,
            func.count(RetrievalLog.id),
            func.sum(case((RetrievalLog.reranked_hits == 0, 1), else_=0)),
            func.sum(case((RetrievalLog.cache_hit.is_(True), 1), else_=0)),
            func.count(distinct(RetrievalLog.user_id)),
        )
        .outerjoin(Department, Department.id == RetrievalLog.department_id)
        .where(RetrievalLog.created_at >= since)
        .group_by(RetrievalLog.department_id, Department.name)
        .order_by(func.count(RetrievalLog.id).desc())
    )
    if department_id is not None:
        statement = statement.where(RetrievalLog.department_id == department_id)
    rows = (await db.execute(statement)).all()
    return [
        {
            "department_id": row[0],
            "department_name": row[1] or "未分配部门",
            "questions": int(row[2] or 0),
            "zero_citation": int(row[3] or 0),
            "cache_hits": int(row[4] or 0),
            "active_users": int(row[5] or 0),
        }
        for row in rows
    ]


def review_next_date(days: int) -> date:
    return date.today() + timedelta(days=max(int(days or 180), 1))
