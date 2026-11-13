import asyncio
import sys
from pathlib import Path

import pymysql
from alembic import command
from alembic.config import Config
from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.init_admin import ensure_admin  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.db.models import Department, StrategyPreset  # noqa: E402
from src.db.session import async_session_factory, engine  # noqa: E402


def create_database() -> None:
    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()
    print(f"数据库就绪：{settings.mysql_database}")


def run_migrations() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    print("数据库迁移完成")


async def seed_departments(db) -> None:
    defaults = [
        ("教务处", "JWC"),
        ("待分配", "PENDING"),
    ]
    for name, code in defaults:
        result = await db.execute(select(Department).where(Department.code == code))
        if result.scalar_one_or_none() is None:
            db.add(Department(name=name, code=code, status="active"))
    await db.commit()


async def seed_defaults() -> None:
    async with async_session_factory() as db:
        await seed_departments(db)
        existing = await db.execute(select(StrategyPreset).limit(1))
        if existing.scalar_one_or_none() is None:
            db.add_all(
                [
                    StrategyPreset(
                        name="快速问答",
                        description="混合检索后精排，适合大多数制度问题",
                        use_vector=True,
                        use_bm25=True,
                        vector_weight=0.5,
                        bm25_weight=0.5,
                        rerank=True,
                        is_open=True,
                        is_default=True,
                    ),
                    StrategyPreset(
                        name="向量优先",
                        description="加大向量召回权重，适合口语化描述",
                        use_vector=True,
                        use_bm25=True,
                        vector_weight=0.7,
                        bm25_weight=0.3,
                        rerank=True,
                        is_open=True,
                        is_default=False,
                    ),
                    StrategyPreset(
                        name="关键词优先",
                        description="加大 BM25 权重，适合条款号与文号检索",
                        use_vector=True,
                        use_bm25=True,
                        vector_weight=0.3,
                        bm25_weight=0.7,
                        rerank=True,
                        is_open=True,
                        is_default=False,
                    ),
                ]
            )
            print("已写入默认检索策略")

        await db.commit()


if __name__ == "__main__":
    create_database()
    run_migrations()

    async def finalize() -> None:
        await seed_defaults()
        await ensure_admin()
        await engine.dispose()

    asyncio.run(finalize())
    print("初始化完成")
