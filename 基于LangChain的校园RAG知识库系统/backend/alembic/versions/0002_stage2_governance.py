"""阶段二治理结构

Revision ID: 0002_stage2_governance
Revises: 0001_initial
Create Date: 2026-11-13

新增部门、四角色、文档 ACL、文档版本、评估报告与转办字段。
迁移采用存在性判断，兼容由 0001 按当前模型直接建表的新库。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_stage2_governance"
down_revision: str | None = "0001_initial"
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
    if not _has_table("departments"):
        op.create_table(
            "departments",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("code", sa.String(50), nullable=False, unique=True),
            sa.Column("parent_id", sa.BigInteger(), nullable=True),
            sa.Column("contact", sa.String(200), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    op.execute(
        sa.text(
            "INSERT IGNORE INTO departments (name, code, status, created_at, updated_at) "
            "VALUES ('教务处', 'JWC', 'active', NOW(), NOW())"
        )
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO departments (name, code, status, created_at, updated_at) "
            "VALUES ('待分配', 'PENDING', 'active', NOW(), NOW())"
        )
    )

    _add_column("users", sa.Column("department_id", sa.BigInteger(), nullable=True))
    _add_fk("users", "fk_users_department", "departments", ["id"], ["department_id"])
    _add_index("users", "ix_users_department_id", ["department_id"])
    op.execute(sa.text("UPDATE users SET role = 'student' WHERE role = 'user'"))
    op.execute(
        sa.text(
            "UPDATE users u JOIN departments d ON d.name = u.department "
            "SET u.department_id = d.id WHERE u.department_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET department_id = "
            "(SELECT id FROM departments WHERE code = 'PENDING') "
            "WHERE department_id IS NULL AND role <> 'admin'"
        )
    )

    _add_column("knowledge_bases", sa.Column("department_id", sa.BigInteger(), nullable=True))
    _add_fk(
        "knowledge_bases",
        "fk_knowledge_bases_department",
        "departments",
        ["id"],
        ["department_id"],
    )
    _add_column(
        "knowledge_bases",
        sa.Column("chunk_strategy", sa.String(20), nullable=False, server_default="clause"),
    )
    op.execute(
        sa.text(
            "UPDATE knowledge_bases SET department_id = "
            "(SELECT id FROM departments WHERE code = 'JWC') WHERE department_id IS NULL"
        )
    )

    _add_column("documents", sa.Column("title", sa.String(255), nullable=False, server_default=""))
    _add_column("documents", sa.Column("doc_no", sa.String(100), nullable=True))
    _add_column("documents", sa.Column("department_id", sa.BigInteger(), nullable=True))
    _add_column(
        "documents",
        sa.Column("visibility", sa.String(20), nullable=False, server_default="department"),
    )
    _add_column("documents", sa.Column("owner_user_id", sa.BigInteger(), nullable=True))
    _add_column("documents", sa.Column("current_version_id", sa.BigInteger(), nullable=True))
    _add_column("documents", sa.Column("review_due_date", sa.Date(), nullable=True))
    _add_column(
        "documents",
        sa.Column("doc_status", sa.String(20), nullable=False, server_default="active"),
    )
    _add_fk("documents", "fk_documents_department", "departments", ["id"], ["department_id"])
    _add_fk("documents", "fk_documents_owner", "users", ["id"], ["owner_user_id"])
    _add_index(
        "documents",
        "ix_documents_kb_visibility",
        ["kb_id", "visibility", "department_id"],
    )
    _add_index("documents", "ix_documents_review_due", ["review_due_date"])
    op.execute(sa.text("UPDATE documents SET title = filename WHERE title = ''"))

    if not _has_table("document_versions"):
        op.create_table(
            "document_versions",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "document_id",
                sa.BigInteger(),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_label", sa.String(50), nullable=False, server_default="v1"),
            sa.Column("file_path", sa.String(500), nullable=False),
            sa.Column("file_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("file_type", sa.String(10), nullable=False, server_default=""),
            sa.Column("file_size", sa.BigInteger(), nullable=True),
            sa.Column(
                "process_status", sa.String(20), nullable=False, server_default="pending"
            ),
            sa.Column(
                "lifecycle_status", sa.String(20), nullable=False, server_default="draft"
            ),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("repealed_at", sa.DateTime(), nullable=True),
            sa.Column("superseded_by_version_id", sa.BigInteger(), nullable=True),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("table_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("clause_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_msg", sa.Text(), nullable=True),
            sa.Column("published_by", sa.BigInteger(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("ingest_report", sa.JSON(), nullable=True),
            sa.Column("published_doc_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("document_id", "version_label", name="uq_document_versions_label"),
        )
        op.create_index(
            "ix_document_versions_document_lifecycle",
            "document_versions",
            ["document_id", "lifecycle_status"],
        )

    _add_column("document_versions", sa.Column("published_doc_id", sa.BigInteger(), nullable=True))
    if not _has_index("document_versions", "uq_document_versions_published"):
        op.create_index(
            "uq_document_versions_published",
            "document_versions",
            ["published_doc_id"],
            unique=True,
        )

    op.execute(
        sa.text(
            "INSERT INTO document_versions "
            "(document_id, version_label, file_path, file_hash, file_type, file_size, "
            "process_status, lifecycle_status, effective_date, chunk_count, published_at, "
            "published_doc_id, created_at, updated_at) "
            "SELECT d.id, 'v1', d.stored_path, '', d.file_type, d.size_bytes, "
            "CASE WHEN d.status IN ('ready', 'completed') THEN 'completed' ELSE 'pending' END, "
            "CASE WHEN d.status IN ('ready', 'completed') THEN 'published' ELSE 'draft' END, "
            "CURDATE(), d.chunk_count, NOW(), "
            "CASE WHEN d.status IN ('ready', 'completed') THEN d.id ELSE NULL END, "
            "NOW(), NOW() "
            "FROM documents d "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM document_versions v WHERE v.document_id = d.id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE document_versions SET published_doc_id = document_id "
            "WHERE lifecycle_status = 'published' AND repealed_at IS NULL "
            "AND published_doc_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE documents d JOIN document_versions v "
            "ON v.document_id = d.id AND v.version_label = 'v1' "
            "SET d.current_version_id = v.id WHERE d.current_version_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE documents SET visibility = 'public' "
            "WHERE current_version_id IS NOT NULL AND department_id IS NULL "
            "AND (visibility IS NULL OR visibility = '' OR visibility = 'department')"
        )
    )

    _add_column("feedback", sa.Column("kind", sa.String(20), nullable=False, server_default="feedback"))
    _add_column("feedback", sa.Column("assigned_department_id", sa.BigInteger(), nullable=True))
    _add_column("feedback", sa.Column("assigned_user_id", sa.BigInteger(), nullable=True))
    _add_column("feedback", sa.Column("transfer_reason", sa.Text(), nullable=True))
    _add_column("feedback", sa.Column("first_response_at", sa.DateTime(), nullable=True))
    _add_index("feedback", "ix_feedback_assigned_department", ["assigned_department_id", "status"])

    _add_column("retrieval_logs", sa.Column("role", sa.String(20), nullable=True))
    _add_column("retrieval_logs", sa.Column("department_id", sa.BigInteger(), nullable=True))
    _add_column("retrieval_logs", sa.Column("visibility_scope", sa.String(100), nullable=True))
    _add_column("retrieval_logs", sa.Column("document_versions", sa.JSON(), nullable=True))

    if not _has_table("evaluation_reports"):
        op.create_table(
            "evaluation_reports",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(120), nullable=False, server_default=""),
            sa.Column("kind", sa.String(20), nullable=False, server_default="retrieval"),
            sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
            sa.Column("testset", sa.String(300), nullable=True),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("report", sa.JSON(), nullable=True),
            sa.Column("markdown", sa.Text(), nullable=True),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_evaluation_reports_created", "evaluation_reports", ["created_at"])


def downgrade() -> None:
    if _has_table("evaluation_reports"):
        _drop_index("evaluation_reports", "ix_evaluation_reports_created")
        op.drop_table("evaluation_reports")

    _drop_column("retrieval_logs", "document_versions")
    _drop_column("retrieval_logs", "visibility_scope")
    _drop_column("retrieval_logs", "department_id")
    _drop_column("retrieval_logs", "role")

    _drop_index("feedback", "ix_feedback_assigned_department")
    _drop_column("feedback", "first_response_at")
    _drop_column("feedback", "transfer_reason")
    _drop_column("feedback", "assigned_user_id")
    _drop_column("feedback", "assigned_department_id")
    _drop_column("feedback", "kind")

    if _has_table("document_versions"):
        _drop_index("document_versions", "uq_document_versions_published")
        _drop_index("document_versions", "ix_document_versions_document_lifecycle")
        op.drop_table("document_versions")

    _drop_index("documents", "ix_documents_review_due")
    _drop_index("documents", "ix_documents_kb_visibility")
    if _has_fk("documents", "fk_documents_owner"):
        op.drop_constraint("fk_documents_owner", "documents", type_="foreignkey")
    if _has_fk("documents", "fk_documents_department"):
        op.drop_constraint("fk_documents_department", "documents", type_="foreignkey")
    _drop_column("documents", "doc_status")
    _drop_column("documents", "review_due_date")
    _drop_column("documents", "current_version_id")
    _drop_column("documents", "owner_user_id")
    _drop_column("documents", "visibility")
    _drop_column("documents", "department_id")
    _drop_column("documents", "doc_no")
    _drop_column("documents", "title")

    _drop_column("knowledge_bases", "chunk_strategy")
    if _has_fk("knowledge_bases", "fk_knowledge_bases_department"):
        op.drop_constraint(
            "fk_knowledge_bases_department", "knowledge_bases", type_="foreignkey"
        )
    _drop_column("knowledge_bases", "department_id")

    op.execute(sa.text("UPDATE users SET role = 'user' WHERE role = 'student'"))
    _drop_index("users", "ix_users_department_id")
    if _has_fk("users", "fk_users_department"):
        op.drop_constraint("fk_users_department", "users", type_="foreignkey")
    _drop_column("users", "department_id")

    if _has_table("departments"):
        op.drop_table("departments")
