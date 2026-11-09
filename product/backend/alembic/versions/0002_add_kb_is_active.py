"""为知识库增加启用开关字段。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 启用开关，默认启用
    op.add_column(
        "knowledge_bases",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "is_active")
