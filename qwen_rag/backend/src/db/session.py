"""异步会话工厂与 FastAPI 依赖。

两条路径各自使用独立的引擎：
请求路径直接跑在 Uvicorn 的事件循环里，用带连接池的 ``engine``；
任务路径（Celery worker 或线程池兜底）由 ``asyncio.run`` 另起事件循环，
与请求所在循环不共享，若复用连接池会取到属于旧循环的连接并抛
``RuntimeError: got Future attached to a different loop``，
因此任务侧改用 ``NullPool``（每次取用新建连接、用完即关），跨循环安全。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.core.config import settings
from src.db.base import engine

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


#: 任务侧引擎：不使用连接池，可在任意事件循环中安全重建连接
task_engine: AsyncEngine = create_async_engine(
    settings.database.url,
    echo=settings.database.echo,
    poolclass=NullPool,
)

TaskSessionLocal = async_sessionmaker(
    bind=task_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：yield 一个 AsyncSession，请求结束自动关闭。"""
    async with AsyncSessionLocal() as session:
        yield session
