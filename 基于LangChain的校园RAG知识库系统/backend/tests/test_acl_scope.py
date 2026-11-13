from datetime import datetime

from src.retrieval.scope import RetrievalScope, build_chroma_filter, metadata_allowed

NOW = datetime(2026, 11, 13, 10, 0, 0)
NEVER = "9999-12-31T23:59:59"


def scope(role: str, department_id: int | None = None) -> RetrievalScope:
    return RetrievalScope(
        user_id=1,
        role=role,
        department_id=department_id,
        now=NOW,
        allow_public=True,
        allow_department=role in {"staff", "dept_admin", "admin"} and department_id is not None,
    )


def metadata(**overrides) -> dict:
    base = {
        "lifecycle_status": "published",
        "effective_date": "2026-01-01",
        "repealed_at": NEVER,
        "visibility": "public",
        "department_id": 1,
    }
    base.update(overrides)
    return base


def test_student_filter_only_public():
    where = build_chroma_filter(scope("student"), 3)
    conditions = where["$and"]
    assert {"kb_id": 3} in conditions
    assert {"visibility": {"$eq": "public"}} in conditions
    assert any(item.get("lifecycle_status") == {"$eq": "published"} for item in conditions)


def test_staff_filter_allows_own_department():
    where = build_chroma_filter(scope("staff", 1), 3)
    department_branch = [item for item in where["$and"] if "$or" in item][0]
    assert {"visibility": {"$eq": "public"}} in department_branch["$or"]
    assert {
        "$and": [
            {"visibility": {"$eq": "department"}},
            {"department_id": {"$eq": 1}},
        ]
    } in department_branch["$or"]


def test_admin_filter_does_not_include_admin_only():
    where = build_chroma_filter(scope("admin", 1), 3)
    visibility = [item for item in where["$and"] if "$or" in item][0]["$or"]
    assert {"visibility": {"$eq": "public"}} in visibility
    assert {"visibility": {"$eq": "department"}} in visibility
    assert all("admin_only" not in str(item) for item in visibility)


def test_student_cannot_see_department_document():
    assert metadata_allowed(metadata(visibility="department"), scope("student")) is False


def test_staff_can_see_own_department_only():
    assert metadata_allowed(metadata(visibility="department", department_id=1), scope("staff", 1))
    assert not metadata_allowed(
        metadata(visibility="department", department_id=2), scope("staff", 1)
    )


def test_admin_can_see_any_department_document():
    assert metadata_allowed(metadata(visibility="department", department_id=2), scope("admin", 1))


def test_admin_only_never_recalled():
    assert not metadata_allowed(metadata(visibility="admin_only"), scope("admin", 1))


def test_future_effective_document_not_recalled():
    assert not metadata_allowed(metadata(effective_date="2099-01-01"), scope("student"))


def test_repealed_document_not_recalled():
    assert not metadata_allowed(
        metadata(repealed_at="2026-11-01T00:00:00"), scope("student")
    )


def test_unpublished_document_not_recalled():
    assert not metadata_allowed(metadata(lifecycle_status="draft"), scope("admin", 1))
