"""知识库启用开关（is_active）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 启用开关：默认启用，保证存量知识库升级后仍可用
    op.add_column(
        "knowledge_bases",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "is_active")
