"""为会话增加项目标识字段。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 项目标识，默认空字符串
    op.add_column(
        "conversations",
        sa.Column("app_key", sa.String(length=20), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("conversations", "app_key")
