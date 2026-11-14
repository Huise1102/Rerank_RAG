"""初始表结构

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-13

首版迁移按模型元数据建表，后续变更使用增量迁移。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from src.db import models
    from src.db.base import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from src.db import models
    from src.db.base import Base

    Base.metadata.drop_all(bind=op.get_bind())
