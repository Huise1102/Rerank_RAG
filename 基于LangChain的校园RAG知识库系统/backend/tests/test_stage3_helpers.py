from datetime import date, timedelta

from src.db.models import User
from src.services.agent_settings import DEFAULT_AGENT_SETTINGS, deep_allowed
from src.services.dashboard_service import _percentile, dashboard_scope, review_next_date


def make_user(role: str, department_id: int | None) -> User:
    return User(username="t", password_hash="x", role=role, department_id=department_id)


def test_dashboard_scope():
    assert dashboard_scope(make_user("admin", None), None) is None
    assert dashboard_scope(make_user("admin", 9), 4) == 4
    assert dashboard_scope(make_user("dept_admin", 3), 4) == 3


def test_percentile():
    assert _percentile([], 0.95) == 0
    assert _percentile([10, 20, 30, 40, 50], 0.5) == 30
    assert _percentile([10, 20, 30, 40, 50], 0.95) == 50


def test_review_next_date():
    assert review_next_date(180) == date.today() + timedelta(days=180)


def test_deep_disabled_by_default():
    settings = dict(DEFAULT_AGENT_SETTINGS)
    assert not deep_allowed(settings, make_user("admin", None), 1)


def test_deep_allowed_by_role_and_kb():
    settings = {
        "enabled": True,
        "roles": ["admin", "dept_admin"],
        "kb_ids": [2, 3],
    }
    assert deep_allowed(settings, make_user("dept_admin", 3), 2)
    assert not deep_allowed(settings, make_user("staff", 3), 2)
    assert not deep_allowed(settings, make_user("dept_admin", 3), 9)
    settings["kb_ids"] = []
    assert deep_allowed(settings, make_user("dept_admin", 3), 9)
