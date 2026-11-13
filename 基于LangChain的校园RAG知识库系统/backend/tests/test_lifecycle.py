import pytest
from fastapi import HTTPException

from src.db.models import Document, User
from src.evaluation.testset import validate_testset
from src.services.document_service import (
    _validate_visibility,
    can_manage_document,
    can_view_document,
)


def user(role: str, department_id: int | None = 1) -> User:
    return User(
        id=1,
        username="u",
        password_hash="x",
        display_name="测试",
        role=role,
        department_id=department_id,
        status="active",
    )


def document(visibility: str, department_id: int | None = 1) -> Document:
    return Document(
        id=1,
        kb_id=1,
        title="测试制度",
        filename="测试制度.pdf",
        stored_path="x",
        file_type="pdf",
        visibility=visibility,
        department_id=department_id,
        doc_status="active",
    )


def test_visibility_matrix():
    public_doc = document("public")
    department_doc = document("department", 1)
    other_department_doc = document("department", 2)
    admin_doc = document("admin_only")
    assert can_view_document(user("student"), public_doc)
    assert not can_view_document(user("student"), department_doc)
    assert can_view_document(user("staff", 1), department_doc)
    assert not can_view_document(user("staff", 1), other_department_doc)
    assert can_view_document(user("admin"), other_department_doc)
    assert can_view_document(user("admin"), admin_doc)
    assert not can_view_document(user("dept_admin", 1), admin_doc)


def test_manage_scope():
    assert can_manage_document(user("admin"), document("public", None))
    assert can_manage_document(user("dept_admin", 1), document("department", 1))
    assert not can_manage_document(user("dept_admin", 2), document("department", 1))
    assert not can_manage_document(user("staff", 1), document("department", 1))


def test_department_visibility_requires_department():
    with pytest.raises(HTTPException) as exc:
        _validate_visibility(user("dept_admin"), "department", None)
    assert exc.value.status_code == 422


def test_department_admin_cannot_set_admin_only():
    with pytest.raises(HTTPException) as exc:
        _validate_visibility(user("dept_admin"), "admin_only", 1)
    assert exc.value.status_code == 403


def test_testset_validation_reports_missing_fields():
    report = validate_testset([{"id": "1", "question": "转专业条件是什么"}])
    assert report["valid"] is False
    assert any("relevant_doc_ids" in item for item in report["errors"])
