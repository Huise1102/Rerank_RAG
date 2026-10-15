"""阶段 4 —— 后端 API 包。

入口：``from src.api.main import create_app, app``；
启动：``python -m src.api.main`` 或
``uvicorn src.api.main:app --host 127.0.0.1 --port 8100``。

main.py 应用与路由，app.py 别名转发，schemas.py 请求/响应模型，
service.py 检索→生成→引用解析编排，ingest.py 增量入库。
"""

from __future__ import annotations

__all__ = ["create_app", "app"]


def __getattr__(name: str):
    """惰性暴露 ``app`` / ``create_app``，避免导入包时就构造整个应用。"""
    if name in ("app", "create_app"):
        from src.api import main as _main

        return getattr(_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
