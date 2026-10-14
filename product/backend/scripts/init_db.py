"""初始化数据库（scripts/init_db.py）。

职责：
1. 按 ``RAG_DATABASE__URL`` 解析出的库名，用 ``CREATE DATABASE IF NOT EXISTS``
   建库（utf8mb4 / utf8mb4_unicode_ci，否则中文与 emoji 会报错）。
2. 建表：优先执行 Alembic 迁移（``alembic upgrade head``）；若缺少 alembic.ini，
   退化为用 ORM 元数据 ``Base.metadata.create_all`` 直接建表。

用法::

    python scripts/init_db.py            # 建库 + 迁移建表
    python scripts/init_db.py --mode create   # 建库 + create_all（跳过 Alembic）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

#: 后端项目根：scripts/init_db.py -> scripts -> backend
BACKEND_ROOT: Path = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.core.logging import get_logger, setup_logging  # noqa: E402

logger = get_logger("scripts.init_db")


async def ensure_database() -> str:
    """在 MySQL 服务端建库（若不存在）。返回库名。"""
    import aiomysql

    url = make_url(settings.database.url)
    dbname = url.database
    if not dbname:
        raise RuntimeError(f"连接串未包含库名: {settings.database.url}")

    conn = await aiomysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()
    logger.info("数据库就绪: %s", dbname)
    return dbname


async def create_all_tables() -> None:
    """用 ORM 元数据直接建表（Alembic 不可用时的退化路径）。"""
    from src.db import models  # noqa: F401 - 注册全部模型
    from src.db.base import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    logger.info("已按 ORM 元数据建表: %s", ", ".join(sorted(Base.metadata.tables)))


def run_alembic_upgrade() -> bool:
    """执行 Alembic 迁移到 head；缺少 alembic.ini 时返回 False。"""
    ini = BACKEND_ROOT / "alembic.ini"
    if not ini.exists():
        logger.warning("未找到 alembic.ini，跳过迁移")
        return False

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database.url)
    command.upgrade(cfg, "head")
    logger.info("Alembic 迁移已执行到 head")
    return True


def _main(mode: str) -> None:
    # 注意：Alembic 的 env.py 内部会调用 asyncio.run()，必须在一个没有
    # 运行中的事件循环的同步上下文里执行，因此这里把异步步骤各自单独 run。
    setup_logging()
    settings.ensure_dirs()
    asyncio.run(ensure_database())

    if mode == "alembic" and run_alembic_upgrade():
        return
    asyncio.run(create_all_tables())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化 MySQL 库与表")
    parser.add_argument(
        "--mode",
        choices=("alembic", "create"),
        default="alembic",
        help="建表方式：alembic=执行迁移（默认）；create=直接 create_all",
    )
    args = parser.parse_args()
    _main(args.mode)
