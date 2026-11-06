"""会话按项目隔离（app_key）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧会话保持空值，不会出现在任何项目的会话列表里
    op.add_column(
        "conversations",
        sa.Column("app_key", sa.String(length=20), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("conversations", "app_key")
