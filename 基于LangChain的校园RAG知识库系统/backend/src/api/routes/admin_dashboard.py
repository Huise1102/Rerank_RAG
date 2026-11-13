from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_document_manager
from src.db.models import User
from src.db.session import get_db
from src.services.dashboard_service import (
    dashboard_scope,
    dashboard_summary,
    dashboard_trends,
    department_comparison,
)

router = APIRouter(prefix="/admin/dashboard", tags=["运营看板"])


@router.get("/summary")
async def read_summary(
    days: int = Query(default=7, ge=1, le=90),
    department_id: int | None = Query(default=None),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    scope_department = dashboard_scope(user, department_id)
    data = await dashboard_summary(db, days, scope_department)
    data["department_id"] = scope_department
    data["role"] = user.role
    return data


@router.get("/trends")
async def read_trends(
    days: int = Query(default=14, ge=1, le=90),
    department_id: int | None = Query(default=None),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    scope_department = dashboard_scope(user, department_id)
    return {"items": await dashboard_trends(db, days, scope_department)}


@router.get("/departments")
async def read_departments(
    days: int = Query(default=30, ge=1, le=90),
    department_id: int | None = Query(default=None),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    scope_department = dashboard_scope(user, department_id)
    return {"items": await department_comparison(db, days, scope_department)}
