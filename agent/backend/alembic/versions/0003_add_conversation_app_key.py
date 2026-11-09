"""会话按项目隔离。迁移版本 0003，前置版本 0002，创建日期 2026 年 11 月 6 日。"""


from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 默认为空字符串，空值会话不归属任何项目
    op.add_column(
        "conversations",
        sa.Column("app_key", sa.String(length=20), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("conversations", "app_key")
