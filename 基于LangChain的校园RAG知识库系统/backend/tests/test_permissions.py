import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.deps import get_current_user, require_admin, require_document_manager
from src.db.models import User
from src.main import create_app
from src.schemas.chat import ChatRequest


def make_user(role: str) -> User:
    return User(id=1, username="tester", password_hash="x", display_name="测试", role=role, status="active")


def collect_dependencies(dependant, seen: set | None = None) -> set:
    """递归收集路由依赖的处理函数。"""

    seen = seen or set()
    for dependency in dependant.dependencies:
        seen.add(dependency.call)
        collect_dependencies(dependency, seen)
    return seen


def routes_by_path(app) -> list:
    return [route for route in app.routes if getattr(route, "dependant", None) is not None]


def find_route(app, path: str, method: str):
    for route in routes_by_path(app):
        if route.path == path and method in route.methods:
            return route
    return None


def test_require_admin_rejects_student() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_admin(make_user("student")))
    assert exc.value.status_code == 403


def test_require_admin_accepts_admin() -> None:
    admin = make_user("admin")
    assert asyncio.run(require_admin(admin)) is admin


def test_require_document_manager_rejects_staff() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_document_manager(make_user("staff")))
    assert exc.value.status_code == 403


def test_require_document_manager_accepts_department_admin() -> None:
    manager = make_user("dept_admin")
    assert asyncio.run(require_document_manager(manager)) is manager


def test_admin_routes_require_manager_or_admin_dependency() -> None:
    app = create_app()
    admin_routes = [route for route in routes_by_path(app) if route.path.startswith("/api/v1/admin")]
    assert admin_routes, "应存在管理端接口"
    for route in admin_routes:
        dependencies = collect_dependencies(route.dependant)
        assert (
            require_admin in dependencies or require_document_manager in dependencies
        ), f"{route.path} 缺少管理端鉴权"


def test_knowledge_write_routes_require_manager() -> None:
    app = create_app()
    protected = [
        ("/api/v1/kb", "POST"),
        ("/api/v1/kb/{kb_id}", "PATCH"),
        ("/api/v1/kb/{kb_id}", "DELETE"),
        ("/api/v1/kb/{kb_id}/documents", "POST"),
        ("/api/v1/kb/{kb_id}/rebuild", "POST"),
    ]
    for path, method in protected:
        route = find_route(app, path, method)
        assert route is not None, f"{method} {path} 不存在"
        assert require_document_manager in collect_dependencies(
            route.dependant
        ), f"{method} {path} 缺少管理端鉴权"


def test_knowledge_read_routes_require_login() -> None:
    app = create_app()
    route = find_route(app, "/api/v1/kb", "GET")
    assert route is not None, "GET /api/v1/kb 不存在"
    assert get_current_user in collect_dependencies(route.dependant), "/api/v1/kb 缺少登录鉴权"
    documents_route = find_route(app, "/api/v1/kb/{kb_id}/documents", "GET")
    assert documents_route is not None
    assert require_document_manager in collect_dependencies(documents_route.dependant)


def test_chat_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="测试", kb_id=1, temperature=0.9)


def test_chat_request_rejects_model_override() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="测试", kb_id=1, model="qwen3:8b")
