"""阶段三治理结构

Revision ID: 0003_stage3_governance
Revises: 0002_stage2_governance
Create Date: 2026-11-13

新增知识库归属与可见性、责任人与复核周期、索引版本、部门排序、
检索日志缓存命中标记、站内通知与知识缺口表。
迁移采用存在性判断，兼容已按当前模型直接建表的新库。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stage3_governance"
down_revision: str | None = "0002_stage2_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {item["name"] for item in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return index in {item["name"] for item in _inspector().get_indexes(table)}


def _has_fk(table: str, name: str) -> bool:
    return name in {item["name"] for item in _inspector().get_foreign_keys(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _drop_column(table: str, column: str) -> None:
    if _has_column(table, column):
        op.drop_column(table, column)


def _add_index(table: str, name: str, columns: list[str], unique: bool = False) -> None:
    if not _has_index(table, name):
        op.create_index(name, table, columns, unique=unique)


def _drop_index(table: str, name: str) -> None:
    if _has_index(table, name):
        op.drop_index(name, table_name=table)


def _add_fk(table: str, name: str, ref_table: str, ref_columns: list[str], local_columns: list[str]) -> None:
    if not _has_fk(table, name):
        op.create_foreign_key(name, table, ref_table, local_columns, ref_columns)


def upgrade() -> None:
    _add_column(
        "departments",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    _add_column(
        "knowledge_bases",
        sa.Column("visibility", sa.String(20), nullable=False, server_default="department"),
    )
    _add_column("knowledge_bases", sa.Column("owner_user_id", sa.BigInteger(), nullable=True))
    _add_column(
        "knowledge_bases",
        sa.Column("review_cycle_days", sa.Integer(), nullable=False, server_default="180"),
    )
    _add_column(
        "knowledge_bases",
        sa.Column("index_epoch", sa.Integer(), nullable=False, server_default="1"),
    )
    _add_fk(
        "knowledge_bases",
        "fk_knowledge_bases_owner",
        "users",
        ["id"],
        ["owner_user_id"],
    )
    _add_index("knowledge_bases", "ix_knowledge_bases_department", ["department_id"])
    op.execute(
        sa.text("UPDATE knowledge_bases SET visibility = 'public'")
    )
    op.execute(
        sa.text(
            "UPDATE knowledge_bases SET owner_user_id = created_by "
            "WHERE owner_user_id IS NULL AND created_by IS NOT NULL"
        )
    )

    _add_column(
        "retrieval_logs",
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    if not _has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.BigInteger(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("channel", sa.String(20), nullable=False, server_default="in_app"),
            sa.Column("type", sa.String(20), nullable=False, server_default="system"),
            sa.Column("title", sa.String(200), nullable=False, server_default=""),
            sa.Column("content", sa.String(500), nullable=False, server_default=""),
            sa.Column("link", sa.String(300), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
            sa.Column("error", sa.String(300), nullable=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
        )
    _add_index("notifications", "ix_notifications_user_read", ["user_id", "read_at"])
    _add_index("notifications", "ix_notifications_created", ["created_at"])

    if not _has_table("knowledge_gaps"):
        op.create_table(
            "knowledge_gaps",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("kb_id", sa.BigInteger(), nullable=True),
            sa.Column("department_id", sa.BigInteger(), nullable=True),
            sa.Column("question", sa.String(500), nullable=False, server_default=""),
            sa.Column("normalized", sa.String(500), nullable=False, server_default=""),
            sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("sources", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="open"),
            sa.Column("category", sa.String(20), nullable=True),
            sa.Column("assigned_user_id", sa.BigInteger(), nullable=True),
            sa.Column("related_document_id", sa.BigInteger(), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    _add_index("knowledge_gaps", "ix_knowledge_gaps_status_seen", ["status", "last_seen_at"])
    _add_index("knowledge_gaps", "ix_knowledge_gaps_kb_normalized", ["kb_id", "normalized"])


def downgrade() -> None:
    if _has_table("knowledge_gaps"):
        op.drop_table("knowledge_gaps")

    if _has_table("notifications"):
        op.drop_table("notifications")

    _drop_column("retrieval_logs", "cache_hit")

    if _has_fk("knowledge_bases", "fk_knowledge_bases_owner"):
        op.drop_constraint("fk_knowledge_bases_owner", "knowledge_bases", type_="foreignkey")
    _drop_column("knowledge_bases", "index_epoch")
    _drop_column("knowledge_bases", "review_cycle_days")
    _drop_column("knowledge_bases", "owner_user_id")
    _drop_column("knowledge_bases", "visibility")

    _drop_column("departments", "sort_order")
