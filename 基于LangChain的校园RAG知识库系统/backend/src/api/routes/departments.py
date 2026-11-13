from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, require_admin, require_document_manager
from src.db.models import AuditLog, Department, User, utcnow
from src.db.session import get_db
from src.schemas.department import (
    DepartmentCreateRequest,
    DepartmentOut,
    DepartmentUpdateRequest,
)

router = APIRouter(prefix="/admin/departments", tags=["部门管理"])


def _audit(db: AsyncSession, admin: User, action: str, target_id: int, detail: str, ip: str) -> None:
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action=action,
            target_type="department",
            target_id=str(target_id),
            detail=detail,
            ip=ip,
        )
    )


async def _validate_parent(
    db: AsyncSession, parent_id: int | None, department_id: int | None = None
) -> None:
    if parent_id is None:
        return
    if department_id is not None and parent_id == department_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="上级部门不能是自身")
    parent = await db.get(Department, parent_id)
    if parent is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="上级部门不存在")
    current = parent
    visited = {parent_id}
    while current.parent_id:
        if current.parent_id in visited:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="上级部门不能形成循环")
        if department_id is not None and current.parent_id == department_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="上级部门不能形成循环")
        visited.add(current.parent_id)
        current = await db.get(Department, current.parent_id)
        if current is None:
            break


@router.get("", response_model=list[DepartmentOut])
async def list_departments(
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    # 部门管理员在文档治理页面需要读取部门下拉，写操作仍限系统管理员
    result = await db.execute(select(Department).order_by(Department.sort_order, Department.id))
    return result.scalars().all()


@router.post("", response_model=DepartmentOut)
async def create_department(
    payload: DepartmentCreateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    duplicated = await db.execute(
        select(Department.id).where(
            (Department.name == payload.name) | (Department.code == payload.code)
        )
    )
    if duplicated.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="部门名称或编码已存在")

    department = Department(
        name=payload.name,
        code=payload.code,
        parent_id=payload.parent_id,
        contact=payload.contact,
        status=payload.status,
        sort_order=payload.sort_order,
    )
    await _validate_parent(db, department.parent_id)
    db.add(department)
    await db.flush()
    _audit(db, admin, "create_department", department.id, department.name, client_ip(request))
    await db.commit()
    await db.refresh(department)
    return department


@router.patch("/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: int,
    payload: DepartmentUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    department = await db.get(Department, department_id)
    if department is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="部门不存在")

    if payload.name is not None and payload.name != department.name:
        duplicated = await db.execute(
            select(Department.id).where(
                Department.name == payload.name, Department.id != department_id
            )
        )
        if duplicated.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="部门名称已存在")
        department.name = payload.name
    if "parent_id" in payload.model_fields_set:
        await _validate_parent(db, payload.parent_id, department_id)
        department.parent_id = payload.parent_id
    if "contact" in payload.model_fields_set:
        department.contact = payload.contact
    if payload.status is not None:
        department.status = payload.status
    if payload.sort_order is not None:
        department.sort_order = payload.sort_order
    department.updated_at = utcnow()
    _audit(db, admin, "update_department", department.id, department.name, client_ip(request))
    await db.commit()
    await db.refresh(department)
    return department
