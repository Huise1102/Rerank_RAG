from datetime import datetime

from src.retrieval.scope import RetrievalScope
from src.services.cache_service import (
    build_cache_key,
    build_semantic_key,
    cosine_similarity,
    normalize_question,
    scope_fingerprint,
)


def make_scope(role: str, department_id: int | None) -> RetrievalScope:
    return RetrievalScope(
        user_id=1,
        role=role,
        department_id=department_id,
        now=datetime(2026, 11, 13, 9, 0, 0),
        allow_public=True,
        allow_department=role in {"staff", "dept_admin", "admin"} and department_id is not None,
    )


def test_normalize_question_unifies_width_space_and_case():
    assert normalize_question("  教务处  成绩单 Ａ４  ") == "教务处 成绩单 a4"
    assert normalize_question("How   To\tPRINT") == "how to print"


def test_cache_key_differs_by_kb_and_epoch():
    scope = make_scope("staff", 3)
    first = build_cache_key(1, 2, scope, 1, "如何补办学生证")
    second = build_cache_key(2, 2, scope, 1, "如何补办学生证")
    third = build_cache_key(1, 2, scope, 2, "如何补办学生证")
    assert first != second
    assert first != third


def test_cache_key_differs_by_permission_scope():
    student = build_cache_key(1, 2, make_scope("student", 3), 1, "成绩单怎么打印")
    staff = build_cache_key(1, 2, make_scope("staff", 3), 1, "成绩单怎么打印")
    other_department = build_cache_key(1, 2, make_scope("staff", 4), 1, "成绩单怎么打印")
    assert student != staff
    assert staff != other_department


def test_scope_fingerprint_is_stable():
    scope = make_scope("dept_admin", 5)
    assert scope_fingerprint(scope) == scope_fingerprint(make_scope("dept_admin", 5))
    assert len(scope_fingerprint(scope)) == 16


def test_semantic_key_isolated_by_permission_and_epoch():
    student = build_semantic_key(1, 2, make_scope("student", 3), 1)
    staff = build_semantic_key(1, 2, make_scope("staff", 3), 1)
    next_epoch = build_semantic_key(1, 2, make_scope("student", 3), 2)
    assert student != staff
    assert student != next_epoch


def test_cosine_similarity_matches_direction():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert round(cosine_similarity([1.0, 1.0], [2.0, 2.0]), 4) == 1.0
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0
