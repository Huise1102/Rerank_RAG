"""知识库启用开关。迁移版本 0002，前置版本 0001，创建日期 2026 年 10 月 14 日。"""


from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 启用开关，默认启用，已有行填 1
    op.add_column(
        "knowledge_bases",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "is_active")
