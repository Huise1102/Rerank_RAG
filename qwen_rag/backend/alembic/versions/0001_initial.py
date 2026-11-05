"""初始建表（与迁移规格文档第 3 章 DDL 一致）。

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_MYSQL = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}
_NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    # 知识库表
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("chunk_strategy", sa.String(50), nullable=False, server_default="recursive"),
        sa.Column("enabled_strategies", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=_NOW),
        **_MYSQL,
    )

    # 文档表
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_type", sa.Enum("pdf", "docx", "txt", "md", name="file_type"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "parsing", "indexing", "completed", "failed", name="doc_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(
            ["kb_id"], ["knowledge_bases.id"], name="fk_documents_kb", ondelete="CASCADE"
        ),
        **_MYSQL,
    )
    op.create_index("idx_kb_status", "documents", ["kb_id", "status"])

    # 会话表
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(
            ["kb_id"], ["knowledge_bases.id"], name="fk_conversations_kb", ondelete="CASCADE"
        ),
        **_MYSQL,
    )
    op.create_index("idx_kb", "conversations", ["kb_id"])

    # 消息表
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", name="message_role"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("retrieved_docs", sa.JSON(), nullable=True),
        sa.Column("latency", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
        **_MYSQL,
    )
    op.create_index("idx_conversation", "messages", ["conversation_id"])

    # 检索日志表
    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kb_id", sa.BigInteger(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(50), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("latency", sa.Float(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=_NOW),
        **_MYSQL,
    )
    op.create_index("idx_kb_created", "retrieval_logs", ["kb_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_kb_created", table_name="retrieval_logs")
    op.drop_table("retrieval_logs")

    op.drop_index("idx_conversation", table_name="messages")
    op.drop_table("messages")

    op.drop_index("idx_kb", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("idx_kb_status", table_name="documents")
    op.drop_table("documents")

    op.drop_table("knowledge_bases")
