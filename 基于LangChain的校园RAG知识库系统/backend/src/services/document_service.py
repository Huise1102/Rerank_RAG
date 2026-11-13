from datetime import date
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog, Department, Document, DocumentVersion, KnowledgeBase, User, utcnow
from src.services.ingest_service import (
    check_index_consistency,
    delete_version_chunks,
    refresh_version_metadata,
)

VISIBILITIES = {"public", "department", "admin_only"}
MANAGE_ROLES = {"admin", "dept_admin"}
DEPARTMENT_VIEW_ROLES = {"admin", "dept_admin", "staff"}


def _audit(
    db: AsyncSession,
    user: User,
    action: str,
    target_type: str,
    target_id: int,
    detail: str,
    ip: str,
) -> None:
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail,
            ip=ip,
        )
    )


async def bump_kb_epoch(db: AsyncSession, kb_id: int) -> None:
    """文档可见性或版本变化时递增知识库索引版本，使旧缓存立即失效。"""
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.index_epoch = int(kb.index_epoch or 1) + 1


def can_manage_document(user: User, document: Document) -> bool:
    if user.role == "admin":
        return True
    if user.role != "dept_admin":
        return False
    return document.department_id is not None and document.department_id == user.department_id


def require_manage(user: User, document: Document) -> None:
    if not can_manage_document(user, document):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限管理该文档")


def can_view_document(user: User, document: Document) -> bool:
    if user.role == "admin":
        return True
    if document.visibility == "public":
        return True
    if document.visibility == "admin_only":
        return False
    if user.role not in DEPARTMENT_VIEW_ROLES:
        return False
    return document.department_id is not None and document.department_id == user.department_id


def visible_document_condition(user: User):
    if user.role == "admin":
        return None
    if user.role in DEPARTMENT_VIEW_ROLES:
        return or_(
            Document.visibility == "public",
            (Document.visibility == "department")
            & (Document.department_id == user.department_id),
        )
    return Document.visibility == "public"


def manage_document_condition(user: User):
    if user.role == "admin":
        return None
    return Document.department_id == user.department_id


async def get_document(db: AsyncSession, document_id: int, user: User, manage: bool = False) -> Document:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    if manage:
        if not can_manage_document(user, document):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    elif not can_view_document(user, document):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return document


async def get_version(db: AsyncSession, version_id: int) -> DocumentVersion:
    version = await db.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="版本不存在")
    return version


async def _resolve_department(db: AsyncSession, department_id: int | None) -> Department | None:
    if department_id is None:
        return None
    department = await db.get(Department, department_id)
    if department is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="部门不存在")
    return department


def _validate_visibility(user: User, visibility: str, department_id: int | None) -> None:
    if visibility not in VISIBILITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="可见性不合法")
    if visibility == "department" and department_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="部门内部文档必须选择归属部门")
    if visibility == "admin_only" and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="仅系统管理员可以设置仅管理员可见")


async def _find_duplicate_hash(
    db: AsyncSession, file_hash: str, document_id: int | None = None
) -> DocumentVersion | None:
    if not file_hash:
        return None
    statement = select(DocumentVersion).where(DocumentVersion.file_hash == file_hash)
    if document_id is not None:
        statement = statement.where(DocumentVersion.document_id != document_id)
    return (await db.execute(statement.limit(1))).scalar_one_or_none()


async def create_document(
    db: AsyncSession,
    user: User,
    kb: KnowledgeBase,
    *,
    file_path: str,
    filename: str,
    file_type: str,
    file_size: int,
    file_hash: str,
    title: str,
    doc_no: str | None,
    department_id: int | None,
    visibility: str,
    owner_user_id: int | None,
    review_due_date: date | None,
    version_label: str,
    effective_date: date | None,
    ip: str,
) -> Document:
    if user.role == "dept_admin" and kb.department_id != user.department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限管理该知识库")
    _validate_visibility(user, visibility, department_id)
    department = await _resolve_department(db, department_id)
    duplicated = await _find_duplicate_hash(db, file_hash)
    if duplicated is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该文件已存在，请勿重复上传")

    document = Document(
        kb_id=kb.id,
        title=title or filename,
        doc_no=doc_no,
        department_id=department.id if department else None,
        visibility=visibility,
        owner_user_id=owner_user_id or user.id,
        review_due_date=review_due_date,
        doc_status="active",
        filename=filename,
        stored_path=file_path,
        file_type=file_type,
        size_bytes=file_size,
        status="pending",
        uploaded_by=user.id,
    )
    db.add(document)
    await db.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_label=version_label or "v1",
        file_path=file_path,
        file_hash=file_hash,
        file_type=file_type,
        file_size=file_size,
        process_status="pending",
        lifecycle_status="draft",
        effective_date=effective_date,
    )
    db.add(version)
    await db.flush()
    _audit(db, user, "create_document", "document", document.id, document.title, ip)
    await db.commit()
    await db.refresh(document)
    return document


async def add_version(
    db: AsyncSession,
    user: User,
    document: Document,
    *,
    file_path: str,
    filename: str,
    file_type: str,
    file_size: int,
    file_hash: str,
    version_label: str,
    effective_date: date | None,
    ip: str,
) -> DocumentVersion:
    require_manage(user, document)
    if document.doc_status == "archived":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="归档文档不能上传新版本")
    duplicated = await db.execute(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_label == version_label,
        )
    )
    if duplicated.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="版本号已存在")
    hash_duplicated = await _find_duplicate_hash(db, file_hash, document.id)
    if hash_duplicated is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该文件已存在，请勿重复上传")
    version = DocumentVersion(
        document_id=document.id,
        version_label=version_label,
        file_path=file_path,
        file_hash=file_hash,
        file_type=file_type,
        file_size=file_size,
        process_status="pending",
        lifecycle_status="draft",
        effective_date=effective_date,
    )
    db.add(version)
    await db.flush()
    _audit(db, user, "add_version", "document_version", version.id, f"{document.title} {version_label}", ip)
    await db.commit()
    await db.refresh(version)
    return version


async def update_document(
    db: AsyncSession,
    user: User,
    document: Document,
    *,
    title: str | None,
    doc_no: str | None,
    department_id: int | None,
    visibility: str | None,
    owner_user_id: int | None,
    review_due_date: date | None,
    ip: str,
) -> Document:
    require_manage(user, document)
    target_visibility = visibility or document.visibility
    target_department = department_id if department_id is not None else document.department_id
    _validate_visibility(user, target_visibility, target_department)
    changes: list[str] = []
    if title is not None and title != document.title:
        document.title = title
        document.filename = title
        changes.append("修改标题")
    if doc_no is not None and doc_no != document.doc_no:
        document.doc_no = doc_no
        changes.append("修改文号")
    if department_id is not None and department_id != document.department_id:
        department = await _resolve_department(db, department_id)
        document.department_id = department.id if department else None
        changes.append("修改归属部门")
    if visibility is not None and visibility != document.visibility:
        document.visibility = visibility
        changes.append("修改可见性")
    if owner_user_id is not None and owner_user_id != document.owner_user_id:
        document.owner_user_id = owner_user_id
        changes.append("修改责任人")
    if review_due_date is not None:
        document.review_due_date = review_due_date
        changes.append("修改复核日期")
    document.updated_at = utcnow()
    if "修改归属部门" in changes or "修改可见性" in changes:
        await bump_kb_epoch(db, document.kb_id)
    _audit(db, user, "update_document", "document", document.id, "；".join(changes) or "无变更", ip)
    await db.commit()
    await db.refresh(document)

    versions = (
        (await db.execute(select(DocumentVersion).where(DocumentVersion.document_id == document.id)))
        .scalars()
        .all()
    )
    for version in versions:
        await refresh_version_metadata(db, version)
    return document


async def publish_version(
    db: AsyncSession, user: User, version: DocumentVersion, ip: str
) -> DocumentVersion:
    document = await db.get(Document, version.document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    require_manage(user, document)
    if document.doc_status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="归档文档不能发布")
    if document.visibility not in VISIBILITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请先设置文档可见性")
    if document.visibility == "department" and document.department_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="部门内部文档必须选择归属部门")
    if version.process_status != "completed" or version.chunk_count <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请先完成解析与索引")
    if version.lifecycle_status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="归档版本不能发布")

    consistency = await check_index_consistency(db, version)
    if not consistency["consistent"]:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "索引不一致，暂不能发布："
                f"数据库 {consistency['db_chunks']} 块，"
                f"向量库 {consistency['chroma_chunks']} 块，"
                f"BM25 {consistency['bm25_chunks']} 块"
            ),
        )

    published = (
        (
            await db.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.lifecycle_status == "published",
                    DocumentVersion.repealed_at.is_(None),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for previous in published:
        if previous.id == version.id:
            continue
        previous.lifecycle_status = "repealed"
        previous.repealed_at = now
        previous.superseded_by_version_id = version.id
        previous.published_doc_id = None
    await db.flush()
    version.lifecycle_status = "published"
    version.published_doc_id = document.id
    version.published_by = user.id
    version.published_at = now
    version.repealed_at = None
    if version.effective_date is None:
        version.effective_date = date.today()
    document.current_version_id = version.id
    document.status = "ready"
    document.error_message = None
    document.chunk_count = version.chunk_count
    document.updated_at = now
    await bump_kb_epoch(db, document.kb_id)
    _audit(
        db,
        user,
        "publish_version",
        "document_version",
        version.id,
        f"{document.title} {version.version_label}",
        ip,
    )
    await db.commit()
    await db.refresh(version)

    for previous in published:
        if previous.id != version.id:
            await refresh_version_metadata(db, previous)
    await refresh_version_metadata(db, version)
    return version


async def repeal_version(
    db: AsyncSession, user: User, version: DocumentVersion, reason: str, ip: str
) -> DocumentVersion:
    document = await db.get(Document, version.document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    require_manage(user, document)
    if version.lifecycle_status != "published":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="仅已发布版本可以废止")
    version.lifecycle_status = "repealed"
    version.repealed_at = utcnow()
    version.published_doc_id = None
    if document.current_version_id == version.id:
        document.current_version_id = None
    await bump_kb_epoch(db, document.kb_id)
    _audit(
        db,
        user,
        "repeal_version",
        "document_version",
        version.id,
        reason or "废止",
        ip,
    )
    await db.commit()
    await db.refresh(version)
    await refresh_version_metadata(db, version)
    return version


async def archive_document(
    db: AsyncSession, user: User, document: Document, reason: str, ip: str
) -> Document:
    require_manage(user, document)
    versions = (
        (
            await db.execute(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.lifecycle_status == "published",
                    DocumentVersion.repealed_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for version in versions:
        version.lifecycle_status = "archived"
        version.repealed_at = now
        version.published_doc_id = None
    document.doc_status = "archived"
    document.current_version_id = None
    document.updated_at = now
    await bump_kb_epoch(db, document.kb_id)
    _audit(db, user, "archive_document", "document", document.id, reason or "归档", ip)
    await db.commit()
    await db.refresh(document)
    for version in versions:
        await refresh_version_metadata(db, version)
    return document


async def rollback_version(
    db: AsyncSession, user: User, version: DocumentVersion, ip: str
) -> DocumentVersion:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="仅系统管理员可以回滚版本")
    document = await db.get(Document, version.document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    if version.process_status != "completed" or version.chunk_count <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="目标版本尚未完成索引")
    if version.lifecycle_status == "archived":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="归档版本不能回滚")
    return await publish_version(db, user, version, ip)


async def remove_document(
    db: AsyncSession, user: User, document: Document, ip: str
) -> None:
    require_manage(user, document)
    versions = (
        (await db.execute(select(DocumentVersion).where(DocumentVersion.document_id == document.id)))
        .scalars()
        .all()
    )
    for version in versions:
        await delete_version_chunks(db, version)
    title = document.title
    document_id = document.id
    await bump_kb_epoch(db, document.kb_id)
    await db.delete(document)
    _audit(db, user, "delete_document", "document", document_id, title, ip)
    await db.commit()
    for version in versions:
        path = Path(version.file_path)
        if path.exists():
            path.unlink()


async def preview_path(
    db: AsyncSession, user: User, document: Document, version_id: int | None, ip: str
) -> Path:
    if not can_view_document(user, document):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    version = None
    if version_id is not None:
        version = await db.get(DocumentVersion, version_id)
        if version is None or version.document_id != document.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
        if not can_manage_document(user, document):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    elif document.current_version_id is not None:
        version = await db.get(DocumentVersion, document.current_version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档尚无可用版本")
    path = Path(version.file_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    _audit(db, user, "preview_document", "document", document.id, "预览原文", ip)
    await db.commit()
    return path
