import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, require_admin
from src.core.security import hash_password
from src.db.models import AuditLog, Department, Document, Feedback, KnowledgeBase, User, utcnow
from src.db.session import get_db
from src.schemas.user import (
    ResetPasswordRequest,
    UserCreateRequest,
    UserListResponse,
    UserOut,
    UserUpdateRequest,
)
from src.services.auth_service import revoke_all_sessions, validate_password_strength

router = APIRouter(prefix="/admin/users", tags=["用户管理"])


def _audit(db: AsyncSession, admin: User, action: str, target_id: int, detail: str, ip: str) -> None:
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action=action,
            target_type="user",
            target_id=str(target_id),
            detail=detail,
            ip=ip,
        )
    )


def _generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


async def _count_active_admins(db: AsyncSession, exclude_id: int | None = None) -> int:
    statement = select(func.count(User.id)).where(User.role == "admin", User.status == "active")
    if exclude_id is not None:
        statement = statement.where(User.id != exclude_id)
    return int((await db.execute(statement)).scalar_one())


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    role: str | None = Query(default=None, pattern="^(admin|dept_admin|staff|student)$"),
    department_id: int | None = None,
    status_value: str | None = Query(default=None, alias="status", pattern="^(active|disabled)$"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    statement = select(User)
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(or_(User.username.like(like), User.display_name.like(like)))
    if role:
        statement = statement.where(User.role == role)
    if department_id:
        statement = statement.where(User.department_id == department_id)
    if status_value:
        statement = statement.where(User.status == status_value)

    total = int(
        (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )
    result = await db.execute(
        statement.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    users = result.scalars().all()
    return UserListResponse(
        items=[UserOut.model_validate(item) for item in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserOut)
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    validate_password_strength(payload.password)
    exists = await db.execute(select(User.id).where(User.username == payload.username))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="用户名已存在")

    department = None
    if payload.department_id is not None:
        department = await db.get(Department, payload.department_id)
        if department is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="部门不存在")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role=payload.role,
        department_id=department.id if department else None,
        department=department.name if department else payload.department,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    _audit(db, admin, "create_user", user.id, f"创建用户 {user.username}", client_ip(request))
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")

    if payload.status == "disabled":
        if target.id == admin.id:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="不能禁用自己")
        if target.role == "admin" and await _count_active_admins(db, exclude_id=target.id) == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="不能禁用最后一个管理员")

    if payload.role is not None and payload.role != "admin" and target.role == "admin":
        if target.id == admin.id:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="不能移除自己的管理员角色")
        if await _count_active_admins(db, exclude_id=target.id) == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="不能移除最后一个管理员")

    department = None
    if payload.department_id is not None:
        department = await db.get(Department, payload.department_id)
        if department is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="部门不存在")

    changes: list[str] = []
    if payload.display_name is not None and payload.display_name != target.display_name:
        target.display_name = payload.display_name
        changes.append("修改姓名")
    if payload.role is not None and payload.role != target.role:
        target.role = payload.role
        changes.append(f"角色改为 {payload.role}")
    if payload.status is not None and payload.status != target.status:
        target.status = payload.status
        changes.append(f"状态改为 {payload.status}")
    if department is not None and department.id != target.department_id:
        target.department_id = department.id
        target.department = department.name
        changes.append(f"部门改为 {department.name}")
    elif payload.department is not None and payload.department != target.department:
        target.department = payload.department
        changes.append("修改部门")
    target.updated_at = utcnow()

    if target.status == "disabled":
        await revoke_all_sessions(db, target.id)
    _audit(db, admin, "update_user", target.id, "；".join(changes) or "无变更", client_ip(request))
    await db.commit()
    await db.refresh(target)
    return UserOut.model_validate(target)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    new_password = payload.new_password or _generate_password()
    validate_password_strength(new_password)
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    target.updated_at = utcnow()
    await revoke_all_sessions(db, target.id)
    _audit(db, admin, "reset_password", target.id, "重置密码", client_ip(request))
    await db.commit()
    return {"ok": True, "temporary_password": new_password}


@router.delete("/{user_id}")
async def disable_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if target.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="不能禁用自己")
    if target.role == "admin" and await _count_active_admins(db, exclude_id=target.id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="不能禁用最后一个管理员")
    target.status = "disabled"
    target.updated_at = utcnow()
    await revoke_all_sessions(db, target.id)
    _audit(db, admin, "disable_user", target.id, "禁用账号", client_ip(request))
    await db.commit()
    return {"ok": True}


@router.post("/{user_id}/close")
async def close_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if target.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="不能注销自己")
    if target.role == "admin" and await _count_active_admins(db, exclude_id=target.id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="不能注销最后一个管理员")

    feedback_total = int(
        (
            await db.execute(select(func.count(Feedback.id)).where(Feedback.user_id == target.id))
        ).scalar_one()
    )
    if feedback_total:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"该用户有 {feedback_total} 条反馈工单，只能禁用，不能注销",
        )

    await db.execute(
        update(KnowledgeBase).where(KnowledgeBase.owner_user_id == target.id).values(owner_user_id=None)
    )
    await db.execute(
        update(KnowledgeBase).where(KnowledgeBase.created_by == target.id).values(created_by=None)
    )
    await db.execute(
        update(Document).where(Document.owner_user_id == target.id).values(owner_user_id=None)
    )
    await db.execute(
        update(Document).where(Document.uploaded_by == target.id).values(uploaded_by=None)
    )
    _audit(db, admin, "close_user", target.id, f"注销账号 {target.username}", client_ip(request))
    await db.delete(target)
    await db.commit()
    return {"ok": True}
