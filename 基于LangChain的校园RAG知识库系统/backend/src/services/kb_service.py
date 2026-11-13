from fastapi import HTTPException, status

from src.db.models import KnowledgeBase, User

KB_VISIBILITIES = {"public", "department"}
ROLE_ORDER = {"admin", "dept_admin", "staff", "student"}


def validate_kb_visibility(
    role: str,
    visibility: str,
    department_id: int | None,
    user_department_id: int | None,
) -> None:
    """校验知识库可见性与归属部门是否符合角色范围。"""
    if visibility not in KB_VISIBILITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="知识库可见性不合法")
    if visibility == "public":
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="仅系统管理员可以创建公开知识库")
        if department_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="公开知识库不能绑定部门")
        return
    if department_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="部门知识库必须选择归属部门")
    if role == "dept_admin" and department_id != user_department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能管理本部门知识库")


def can_manage_kb(user: User, kb: KnowledgeBase) -> bool:
    """判断用户是否可以管理指定知识库。

    部门管理员可以管理归属本部门的知识库，包括由系统管理员建立的公开库。
    """
    if user.role == "admin":
        return True
    if user.role != "dept_admin":
        return False
    return kb.department_id is not None and kb.department_id == user.department_id


def can_access_kb(
    role: str,
    department_id: int | None,
    kb_visibility: str,
    kb_department_id: int | None,
    kb_status: str,
    department_status: str | None,
) -> bool:
    """判断用户是否可以对该知识库发起问答。"""
    if role not in ROLE_ORDER:
        return False
    if kb_status != "published":
        return False
    if kb_visibility == "public":
        return True
    if kb_visibility != "department":
        return False
    if role == "admin":
        return True
    if role != "staff" and role != "dept_admin":
        return False
    if department_status != "active":
        return False
    return department_id is not None and kb_department_id == department_id


def can_view_kb_in_management(role: str, department_id: int | None, kb: KnowledgeBase) -> bool:
    """判断用户是否可以在管理端看到指定知识库。"""
    if role == "admin":
        return True
    if role != "dept_admin":
        return False
    if kb.visibility == "public":
        return True
    return kb.department_id is not None and kb.department_id == department_id
