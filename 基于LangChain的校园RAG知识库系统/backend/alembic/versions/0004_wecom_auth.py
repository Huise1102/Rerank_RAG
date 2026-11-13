"""企业微信身份绑定表

Revision ID: 0004_wecom_auth
Revises: 0003_stage3_governance
Create Date: 2026-11-13

新增企业微信外部身份与站内账号的绑定关系，企业微信为唯一的外部身份接入方式。
迁移采用存在性判断，兼容已按当前模型直接建表的新库。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_wecom_auth"
down_revision: str | None = "0003_stage3_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_index(table: str, index: str) -> bool:
    return index in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _has_table("auth_identities"):
        op.create_table(
            "auth_identities",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(20), nullable=False, server_default="wecom"),
            sa.Column("external_id", sa.String(128), nullable=False),
            sa.Column(
                "user_id",
                sa.BigInteger(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "provider", "external_id", name="uq_auth_identities_external"
            ),
        )
    if not _has_index("auth_identities", "ix_auth_identities_user"):
        op.create_index("ix_auth_identities_user", "auth_identities", ["user_id"])


def downgrade() -> None:
    if _has_table("auth_identities"):
        op.drop_table("auth_identities")
