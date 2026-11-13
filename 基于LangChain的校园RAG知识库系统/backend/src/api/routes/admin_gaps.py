import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_admin, require_document_manager
from src.db.models import KnowledgeGap, User
from src.db.session import get_db
from src.services.gap_service import export_gaps, list_gaps, scan_gaps, update_gap

router = APIRouter(prefix="/admin/gaps", tags=["知识缺口"])


class GapUpdateRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|in_progress|resolved|ignored)$")
    category: str | None = Field(
        default=None, pattern="^(missing_document|expired_document|retrieval|model|no_action)$"
    )
    assigned_user_id: int | None = None
    related_document_id: int | None = None


@router.post("/scan")
async def scan_knowledge_gaps(
    days: int = Query(default=30, ge=1, le=180),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await scan_gaps(db, days)


@router.get("")
async def read_gaps(
    status_value: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    kb_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    return await list_gaps(
        db,
        user.role,
        user.department_id,
        status_value=status_value,
        category=category,
        kb_id=kb_id,
        page=page,
        page_size=page_size,
    )


@router.patch("/{gap_id}")
async def update_knowledge_gap(
    gap_id: int,
    payload: GapUpdateRequest,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    gap = await db.get(KnowledgeGap, gap_id)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识缺口不存在")
    if user.role != "admin" and gap.department_id != user.department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能处理本部门知识缺口")
    updated = await update_gap(
        db, user.role, user.department_id, gap, payload.model_dump(exclude_none=True)
    )
    return {
        "id": updated.id,
        "status": updated.status,
        "category": updated.category,
        "assigned_user_id": updated.assigned_user_id,
        "related_document_id": updated.related_document_id,
    }


@router.get("/export")
async def export_knowledge_gaps(
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    rows = await export_gaps(db, user.role, user.department_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["问题", "知识库", "出现次数", "来源", "状态", "分类", "最后出现时间"])
    for item in rows:
        writer.writerow(
            [
                item["question"],
                item.get("kb_name") or "",
                item["occurrences"],
                ",".join(item.get("sources") or []),
                item["status"],
                item.get("category") or "",
                item["last_seen_at"],
            ]
        )
    content = "\ufeff" + buffer.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=knowledge_gaps.csv"},
    )
