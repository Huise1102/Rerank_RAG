import pytest
from fastapi import HTTPException

from src.db.models import KnowledgeBase, User
from src.services.kb_service import (
    can_access_kb,
    can_manage_kb,
    can_view_kb_in_management,
    validate_kb_visibility,
)


def make_user(role: str, department_id: int | None) -> User:
    return User(username="tester", password_hash="x", role=role, department_id=department_id)


def make_kb(visibility: str, department_id: int | None, status: str = "published") -> KnowledgeBase:
    return KnowledgeBase(
        name="测试库",
        description="",
        visibility=visibility,
        department_id=department_id,
        status=status,
    )


def test_admin_can_create_public_kb():
    validate_kb_visibility("admin", "public", None, None)


def test_public_kb_cannot_bind_department():
    with pytest.raises(HTTPException) as exc:
        validate_kb_visibility("admin", "public", 3, None)
    assert exc.value.status_code == 422


def test_department_admin_cannot_create_public_kb():
    with pytest.raises(HTTPException) as exc:
        validate_kb_visibility("dept_admin", "public", None, 3)
    assert exc.value.status_code == 403


def test_department_admin_cannot_bind_other_department():
    with pytest.raises(HTTPException) as exc:
        validate_kb_visibility("dept_admin", "department", 4, 3)
    assert exc.value.status_code == 403


def test_department_kb_requires_department():
    with pytest.raises(HTTPException) as exc:
        validate_kb_visibility("admin", "department", None, None)
    assert exc.value.status_code == 422


def test_student_only_public_kb():
    assert can_access_kb("student", 3, "public", None, "published", None)
    assert not can_access_kb("student", 3, "department", 3, "published", "active")


def test_staff_own_department_kb():
    assert can_access_kb("staff", 3, "department", 3, "published", "active")
    assert not can_access_kb("staff", 3, "department", 4, "published", "active")


def test_staff_blocked_when_department_disabled():
    assert not can_access_kb("staff", 3, "department", 3, "published", "disabled")


def test_admin_can_access_any_department_kb():
    assert can_access_kb("admin", 3, "department", 4, "published", "active")


def test_unpublished_kb_not_answerable():
    assert not can_access_kb("admin", 3, "public", None, "disabled", None)
    assert not can_access_kb("admin", 3, "public", None, "unpublished", None)


def test_manage_scope():
    assert can_manage_kb(make_user("admin", None), make_kb("public", None))
    assert can_manage_kb(make_user("dept_admin", 3), make_kb("department", 3))
    assert can_manage_kb(make_user("dept_admin", 3), make_kb("public", 3))
    assert not can_manage_kb(make_user("dept_admin", 3), make_kb("department", 4))
    assert not can_manage_kb(make_user("dept_admin", 3), make_kb("public", None))
    assert not can_manage_kb(make_user("staff", 3), make_kb("department", 3))


def test_management_visibility():
    assert can_view_kb_in_management("admin", None, make_kb("department", 4))
    assert can_view_kb_in_management("dept_admin", 3, make_kb("public", None))
    assert can_view_kb_in_management("dept_admin", 3, make_kb("department", 3))
    assert not can_view_kb_in_management("dept_admin", 3, make_kb("department", 4))
    assert not can_view_kb_in_management("staff", 3, make_kb("public", None))
