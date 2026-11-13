from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Feedback, KnowledgeBase, KnowledgeGap, RetrievalLog, utcnow
from src.services.cache_service import normalize_question

NEGATIVE_TYPES = ("答案错误", "引用错误或缺失", "回答不完整", "知识库无相关内容", "内容过期")
GAP_STATUSES = {"open", "in_progress", "resolved", "ignored"}
GAP_CATEGORIES = {"missing_document", "expired_document", "retrieval", "model", "no_action"}


def _serialize(gap: KnowledgeGap, kb_name: str | None = None) -> dict:
    return {
        "id": gap.id,
        "kb_id": gap.kb_id,
        "kb_name": kb_name,
        "department_id": gap.department_id,
        "question": gap.question,
        "occurrences": gap.occurrences,
        "sources": gap.sources or [],
        "status": gap.status,
        "category": gap.category,
        "assigned_user_id": gap.assigned_user_id,
        "related_document_id": gap.related_document_id,
        "first_seen_at": gap.first_seen_at,
        "last_seen_at": gap.last_seen_at,
    }


async def scan_gaps(db: AsyncSession, days: int = 30) -> dict:
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    groups: dict[tuple, dict] = {}

    def add(question: str, kb_id: int | None, department_id: int | None, source: str, at: datetime) -> None:
        text = (question or "").strip()
        if not text:
            return
        normalized = normalize_question(text)[:500]
        key = (kb_id, normalized)
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "question": text[:500],
                "kb_id": kb_id,
                "department_id": department_id,
                "normalized": normalized,
                "sources": {source},
                "occurrences": 1,
                "first_seen_at": at,
                "last_seen_at": at,
            }
            return
        group["occurrences"] += 1
        group["sources"].add(source)
        group["first_seen_at"] = min(group["first_seen_at"], at)
        group["last_seen_at"] = max(group["last_seen_at"], at)
        if group["department_id"] is None:
            group["department_id"] = department_id

    zero_rows = (
        await db.execute(
            select(RetrievalLog).where(
                RetrievalLog.created_at >= since, RetrievalLog.reranked_hits == 0
            )
        )
    ).scalars().all()
    for item in zero_rows:
        add(item.question, item.kb_id, item.department_id, "zero_citation", item.created_at)

    feedback_rows = (
        await db.execute(
            select(Feedback).where(
                Feedback.created_at >= since,
                Feedback.kind.in_(["feedback", "transfer"]),
            )
        )
    ).scalars().all()
    for item in feedback_rows:
        if item.kind == "transfer":
            add(item.question_snapshot, item.kb_id, item.assigned_department_id, "transfer", item.created_at)
        elif item.feedback_type in NEGATIVE_TYPES:
            add(
                item.question_snapshot,
                item.kb_id,
                item.assigned_department_id,
                "negative_feedback",
                item.created_at,
            )

    created = 0
    updated = 0
    for key, group in groups.items():
        kb_id, normalized = key
        existing = (
            await db.execute(
                select(KnowledgeGap)
                .where(KnowledgeGap.kb_id == kb_id, KnowledgeGap.normalized == normalized)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            payload = dict(group)
            payload["sources"] = sorted(group["sources"])
            db.add(KnowledgeGap(**payload))
            created += 1
            continue
        existing.question = group["question"]
        existing.occurrences = max(existing.occurrences, group["occurrences"])
        existing.last_seen_at = max(existing.last_seen_at, group["last_seen_at"])
        existing.sources = sorted(set(existing.sources or []) | group["sources"])
        if existing.department_id is None:
            existing.department_id = group["department_id"]
        updated += 1
    await db.commit()
    return {"groups": len(groups), "created": created, "updated": updated}


async def list_gaps(
    db: AsyncSession,
    user_role: str,
    department_id: int | None,
    *,
    status_value: str | None = None,
    category: str | None = None,
    kb_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    statement = select(KnowledgeGap, KnowledgeBase.name).outerjoin(
        KnowledgeBase, KnowledgeBase.id == KnowledgeGap.kb_id
    )
    if user_role != "admin":
        statement = statement.where(KnowledgeGap.department_id == department_id)
    if status_value:
        statement = statement.where(KnowledgeGap.status == status_value)
    if category:
        statement = statement.where(KnowledgeGap.category == category)
    if kb_id:
        statement = statement.where(KnowledgeGap.kb_id == kb_id)
    total = int(
        (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )
    rows = (
        await db.execute(
            statement.order_by(KnowledgeGap.last_seen_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [_serialize(gap, kb_name) for gap, kb_name in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def update_gap(
    db: AsyncSession,
    user_role: str,
    department_id: int | None,
    gap: KnowledgeGap,
    values: dict,
) -> KnowledgeGap:
    if user_role != "admin" and gap.department_id != department_id:
        return gap
    if values.get("status") is not None:
        if values["status"] not in GAP_STATUSES:
            values.pop("status")
        else:
            gap.status = values["status"]
    if values.get("category") is not None:
        if values["category"] in GAP_CATEGORIES:
            gap.category = values["category"]
    if values.get("assigned_user_id") is not None:
        gap.assigned_user_id = values["assigned_user_id"]
    if values.get("related_document_id") is not None:
        gap.related_document_id = values["related_document_id"]
    gap.updated_at = utcnow()
    await db.commit()
    await db.refresh(gap)
    return gap


async def export_gaps(
    db: AsyncSession, user_role: str, department_id: int | None
) -> list[dict]:
    result = await list_gaps(
        db, user_role, department_id, page=1, page_size=1000, status_value="open"
    )
    return result["items"]
