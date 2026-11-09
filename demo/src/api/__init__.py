"""后端 API 包。

main.py 定义应用与路由，app.py 作为别名转发，schemas.py 定义请求与响应模型，service.py 编排检索、生成与引用解析，ingest.py 负责增量入库。
"""

from __future__ import annotations

__all__ = ["create_app", "app"]


def __getattr__(name: str):
    """按需暴露 app 与 create_app，避免导入包时构造整个应用。"""
    if name in ("app", "create_app"):
        from src.api import main as _main

        return getattr(_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
