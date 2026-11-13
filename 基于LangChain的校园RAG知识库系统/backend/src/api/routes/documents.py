import hashlib
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, get_current_user, require_admin, require_document_manager
from src.core.config import settings
from src.db.models import AuditLog, Department, Document, DocumentVersion, KnowledgeBase, User
from src.db.session import get_db
from src.ingest.loaders import SUPPORTED_SUFFIXES
from src.schemas.document import (
    DocumentDetailOut,
    DocumentOut,
    DocumentUpdateRequest,
    DocumentVersionOut,
    VersionActionRequest,
)
from src.services.document_service import (
    add_version,
    archive_document,
    create_document,
    get_document,
    get_version,
    manage_document_condition,
    preview_path,
    publish_version,
    remove_document,
    repeal_version,
    rollback_version,
    update_document,
)
from src.services.ingest_service import check_index_consistency, parse_and_index_version

router = APIRouter(tags=["文档治理"])


def _storage_dir(kb_id: int) -> Path:
    path = settings.storage_path / f"kb_{kb_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _save_upload(file: UploadFile, kb_id: int) -> tuple[str, str, int, str]:
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="仅支持 pdf、docx、txt、md 文件"
        )
    content = await file.read()
    if len(content) > settings.upload_max_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"文件大小不能超过 {settings.upload_max_mb} MB",
        )
    target = _storage_dir(kb_id) / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    return str(target), filename, len(content), file_hash


async def _serialize(
    db: AsyncSession, document: Document, version: DocumentVersion | None = None
) -> dict:
    department = await db.get(Department, document.department_id) if document.department_id else None
    owner = await db.get(User, document.owner_user_id) if document.owner_user_id else None
    data = DocumentOut.model_validate(document).model_dump()
    data["department_name"] = department.name if department else None
    data["owner_name"] = (owner.display_name or owner.username) if owner else None
    data["version"] = (
        DocumentVersionOut.model_validate(version).model_dump() if version else None
    )
    return data


@router.get("/admin/documents")
async def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    kb_id: int | None = None,
    department_id: int | None = None,
    visibility: str | None = None,
    lifecycle: str | None = None,
    keyword: str | None = None,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    statement = select(Document)
    condition = manage_document_condition(user)
    if condition is not None:
        statement = statement.where(condition)
    if kb_id:
        statement = statement.where(Document.kb_id == kb_id)
    if department_id:
        statement = statement.where(Document.department_id == department_id)
    if visibility:
        statement = statement.where(Document.visibility == visibility)
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(
            or_(Document.title.like(like), Document.filename.like(like), Document.doc_no.like(like))
        )
    if lifecycle:
        subquery = (
            select(DocumentVersion.document_id)
            .where(DocumentVersion.lifecycle_status == lifecycle)
            .subquery()
        )
        statement = statement.where(Document.id.in_(select(subquery.c.document_id)))

    total = int(
        (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar_one()
    )
    documents = (
        (
            await db.execute(
                statement.order_by(Document.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    version_ids = [item.current_version_id for item in documents if item.current_version_id]
    versions: dict[int, DocumentVersion] = {}
    if version_ids:
        rows = (
            (await db.execute(select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))))
            .scalars()
            .all()
        )
        versions = {item.id: item for item in rows}
    items = [
        await _serialize(db, item, versions.get(item.current_version_id or 0)) for item in documents
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/admin/documents", response_model=DocumentOut)
async def create_document_route(
    request: Request,
    kb_id: int = Form(...),
    file: UploadFile = File(...),
    title: str = Form(default=""),
    doc_no: str | None = Form(default=None),
    department_id: int | None = Form(default=None),
    visibility: str = Form(default="department"),
    owner_user_id: int | None = Form(default=None),
    review_due_date: date | None = Form(default=None),
    version_label: str = Form(default="v1"),
    effective_date: date | None = Form(default=None),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    if user.role == "dept_admin" and kb.department_id != user.department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限管理该知识库")
    file_path, filename, size, file_hash = await _save_upload(file, kb_id)
    document = await create_document(
        db,
        user,
        kb,
        file_path=file_path,
        filename=filename,
        file_type=Path(filename).suffix.lstrip(".").lower(),
        file_size=size,
        file_hash=file_hash,
        title=title or filename,
        doc_no=doc_no,
        department_id=department_id,
        visibility=visibility,
        owner_user_id=owner_user_id,
        review_due_date=review_due_date,
        version_label=version_label or "v1",
        effective_date=effective_date,
        ip=client_ip(request),
    )
    version = (
        await db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return await _serialize(db, document, version)


@router.get("/admin/documents/{document_id}", response_model=DocumentDetailOut)
async def document_detail(
    document_id: int,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    versions = (
        (
            await db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.id.desc())
            )
        )
        .scalars()
        .all()
    )
    current = next((item for item in versions if item.id == document.current_version_id), None)
    data = await _serialize(db, document, current)
    data["versions"] = [DocumentVersionOut.model_validate(item).model_dump() for item in versions]
    return data


@router.patch("/admin/documents/{document_id}", response_model=DocumentOut)
async def update_document_route(
    document_id: int,
    payload: DocumentUpdateRequest,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    updated = await update_document(
        db,
        user,
        document,
        title=payload.title,
        doc_no=payload.doc_no,
        department_id=payload.department_id,
        visibility=payload.visibility,
        owner_user_id=payload.owner_user_id,
        review_due_date=payload.review_due_date,
        ip=client_ip(request),
    )
    version = (
        await db.get(DocumentVersion, updated.current_version_id)
        if updated.current_version_id
        else None
    )
    return await _serialize(db, updated, version)


@router.post("/admin/documents/{document_id}/versions", response_model=DocumentVersionOut)
async def upload_version(
    document_id: int,
    request: Request,
    file: UploadFile = File(...),
    version_label: str = Form(...),
    effective_date: date | None = Form(default=None),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    file_path, filename, size, file_hash = await _save_upload(file, document.kb_id)
    version = await add_version(
        db,
        user,
        document,
        file_path=file_path,
        filename=filename,
        file_type=Path(filename).suffix.lstrip(".").lower(),
        file_size=size,
        file_hash=file_hash,
        version_label=version_label,
        effective_date=effective_date,
        ip=client_ip(request),
    )
    return DocumentVersionOut.model_validate(version)


@router.post("/admin/versions/{version_id}/parse", response_model=DocumentVersionOut)
async def parse_version(
    version_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    version = await get_version(db, version_id)
    document = await get_document(db, version.document_id, user, manage=True)
    await parse_and_index_version(db, version)
    await db.refresh(version)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="parse_version",
            target_type="document_version",
            target_id=str(version.id),
            detail=f"{document.title} {version.version_label}",
            ip=client_ip(request),
        )
    )
    await db.commit()
    return DocumentVersionOut.model_validate(version)


@router.get("/admin/versions/{version_id}/consistency")
async def version_consistency(
    version_id: int,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    version = await get_version(db, version_id)
    await get_document(db, version.document_id, user, manage=True)
    return await check_index_consistency(db, version)


@router.post("/admin/versions/{version_id}/publish", response_model=DocumentVersionOut)
async def publish_version_route(
    version_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    version = await get_version(db, version_id)
    await get_document(db, version.document_id, user, manage=True)
    updated = await publish_version(db, user, version, client_ip(request))
    return DocumentVersionOut.model_validate(updated)


@router.post("/admin/versions/{version_id}/repeal", response_model=DocumentVersionOut)
async def repeal_version_route(
    version_id: int,
    payload: VersionActionRequest,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    version = await get_version(db, version_id)
    await get_document(db, version.document_id, user, manage=True)
    updated = await repeal_version(db, user, version, payload.reason or "", client_ip(request))
    return DocumentVersionOut.model_validate(updated)


@router.post("/admin/versions/{version_id}/rollback", response_model=DocumentVersionOut)
async def rollback_version_route(
    version_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    version = await get_version(db, version_id)
    updated = await rollback_version(db, user, version, client_ip(request))
    return DocumentVersionOut.model_validate(updated)


@router.post("/admin/documents/{document_id}/archive", response_model=DocumentOut)
async def archive_document_route(
    document_id: int,
    payload: VersionActionRequest,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    updated = await archive_document(db, user, document, payload.reason or "", client_ip(request))
    return await _serialize(db, updated)


@router.delete("/admin/documents/{document_id}")
async def delete_document_route(
    document_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    await remove_document(db, user, document, client_ip(request))
    return {"ok": True}


@router.get("/admin/documents/{document_id}/preview")
async def admin_preview(
    document_id: int,
    request: Request,
    version_id: int | None = None,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    path = await preview_path(db, user, document, version_id, client_ip(request))
    return FileResponse(path, filename=document.filename)


@router.get("/documents/{document_id}/preview")
async def user_preview(
    document_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user)
    path = await preview_path(db, user, document, None, client_ip(request))
    return FileResponse(path, filename=document.filename)
