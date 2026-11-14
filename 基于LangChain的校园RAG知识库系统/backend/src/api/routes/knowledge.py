import shutil
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, get_current_user, require_document_manager
from src.core.config import settings
from src.db.models import (
    AuditLog,
    Conversation,
    Department,
    Document,
    DocumentVersion,
    KnowledgeBase,
    User,
)
from src.db.session import get_db
from src.ingest.loaders import SUPPORTED_SUFFIXES
from src.ingest.pipeline import rebuild_knowledge_base
from src.retrieval import bm25
from src.retrieval.vector_store import delete_collection
from src.schemas.knowledge import (
    DocumentOut,
    KbCreateRequest,
    KbOut,
    KbUpdateRequest,
)
from src.services.document_service import (
    create_document,
    get_document,
    manage_document_condition,
    publish_version,
    remove_document,
)
from src.services.ingest_service import parse_and_index_version
from src.services.kb_service import (
    can_manage_kb,
    validate_kb_visibility,
)

router = APIRouter(tags=["知识库"])


def _audit(db: AsyncSession, user: User, action: str, target_type: str, target_id: str, detail: str, ip: str) -> None:
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip=ip,
        )
    )


def _kb_storage(kb_id: int) -> Path:
    path = settings.storage_path / f"kb_{kb_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_kb_manage(user: User, kb: KnowledgeBase) -> None:
    if not can_manage_kb(user, kb):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限管理该知识库")


def _bump_index_epoch(kb: KnowledgeBase) -> None:
    """知识库检索范围变化时递增索引版本，使旧缓存键失效。"""
    kb.index_epoch = int(kb.index_epoch or 1) + 1


@router.get("/knowledge-bases", response_model=list[KbOut])
async def list_visible_knowledge_bases(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户可用于问答的知识库。"""
    statement = (
        select(KnowledgeBase)
        .outerjoin(Department, Department.id == KnowledgeBase.department_id)
        .where(KnowledgeBase.status == "published")
    )
    if user.role == "student":
        statement = statement.where(KnowledgeBase.visibility == "public")
    elif user.role in {"staff", "dept_admin"}:
        statement = statement.where(
            or_(
                KnowledgeBase.visibility == "public",
                and_(
                    KnowledgeBase.visibility == "department",
                    KnowledgeBase.department_id == user.department_id,
                    Department.status == "active",
                ),
            )
        )
    else:
        statement = statement.where(
            or_(
                KnowledgeBase.visibility == "public",
                and_(
                    KnowledgeBase.visibility == "department",
                    Department.status == "active",
                ),
            )
        )
    result = await db.execute(statement.order_by(KnowledgeBase.id))
    return result.scalars().all()


@router.get("/kb", response_model=list[KbOut])
async def list_knowledge_bases(
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    statement = select(KnowledgeBase)
    if user.role == "dept_admin":
        statement = statement.where(
            or_(
                KnowledgeBase.visibility == "public",
                KnowledgeBase.department_id == user.department_id,
            )
        )
    result = await db.execute(statement.order_by(KnowledgeBase.id))
    return result.scalars().all()


@router.post("/kb", response_model=KbOut)
async def create_knowledge_base(
    payload: KbCreateRequest,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(KnowledgeBase.id).where(KnowledgeBase.name == payload.name))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="知识库名称已存在")
    department_id = payload.department_id
    if user.role == "dept_admin":
        department_id = user.department_id
    validate_kb_visibility(user.role, payload.visibility, department_id, user.department_id)
    kb = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        status=payload.status,
        department_id=department_id,
        visibility=payload.visibility,
        owner_user_id=payload.owner_user_id or user.id,
        review_cycle_days=payload.review_cycle_days,
        chunk_strategy=payload.chunk_strategy,
        created_by=user.id,
    )
    db.add(kb)
    await db.flush()
    _audit(db, user, "create_kb", "knowledge_base", str(kb.id), kb.name, client_ip(request))
    await db.commit()
    await db.refresh(kb)
    _kb_storage(kb.id)
    return kb


@router.patch("/kb/{kb_id}", response_model=KbOut)
async def update_knowledge_base(
    kb_id: int,
    payload: KbUpdateRequest,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    _require_kb_manage(user, kb)

    target_visibility = payload.visibility or kb.visibility
    if user.role == "dept_admin":
        target_visibility = "department"
        target_department = user.department_id
    elif target_visibility == "public":
        target_department = None
    else:
        target_department = (
            payload.department_id if payload.department_id is not None else kb.department_id
        )
    validate_kb_visibility(user.role, target_visibility, target_department, user.department_id)

    scope_changed = False
    if payload.name is not None:
        duplicated = await db.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.name == payload.name, KnowledgeBase.id != kb_id)
        )
        if duplicated.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="知识库名称已存在")
        kb.name = payload.name
    if payload.description is not None:
        kb.description = payload.description
    if payload.status is not None and payload.status != kb.status:
        kb.status = payload.status
        scope_changed = True
    if target_visibility != kb.visibility or target_department != kb.department_id:
        kb.visibility = target_visibility
        kb.department_id = target_department
        scope_changed = True
    if payload.owner_user_id is not None:
        kb.owner_user_id = payload.owner_user_id
    if payload.review_cycle_days is not None:
        kb.review_cycle_days = payload.review_cycle_days
    if payload.chunk_strategy is not None and payload.chunk_strategy != kb.chunk_strategy:
        kb.chunk_strategy = payload.chunk_strategy
        scope_changed = True
    if scope_changed:
        _bump_index_epoch(kb)
    _audit(db, user, "update_kb", "knowledge_base", str(kb.id), kb.name, client_ip(request))
    await db.commit()
    await db.refresh(kb)
    return kb


@router.delete("/kb/{kb_id}")
async def delete_knowledge_base(
    kb_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    _require_kb_manage(user, kb)

    documents = (await db.execute(select(Document).where(Document.kb_id == kb_id))).scalars().all()
    for document in documents:
        await remove_document(db, user, document, client_ip(request))
    conversations = (
        (await db.execute(select(Conversation).where(Conversation.kb_id == kb_id))).scalars().all()
    )
    for conversation in conversations:
        conversation.kb_id = None
    await db.delete(kb)
    _audit(db, user, "delete_kb", "knowledge_base", str(kb_id), kb.name, client_ip(request))
    await db.commit()

    delete_collection(kb_id)
    bm25.invalidate(kb_id)
    index_file = bm25.index_file(kb_id)
    if index_file.exists():
        index_file.unlink()
    storage = settings.storage_path / f"kb_{kb_id}"
    if storage.exists():
        shutil.rmtree(storage, ignore_errors=True)
    return {"ok": True}


@router.get("/kb/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    kb_id: int,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    _require_kb_manage(user, kb)
    statement = select(Document).where(Document.kb_id == kb_id)
    condition = manage_document_condition(user)
    if condition is not None:
        statement = statement.where(condition)
    result = await db.execute(statement.order_by(Document.id.desc()))
    return result.scalars().all()


@router.post("/kb/{kb_id}/documents", response_model=DocumentOut)
async def upload_document(
    kb_id: int,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    doc_no: str | None = Form(default=None),
    department_id: int | None = Form(default=None),
    visibility: str = Form(default=""),
    version_label: str = Form(default="v1"),
    effective_date: date | None = Form(default=None),
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    _require_kb_manage(user, kb)

    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="仅支持 pdf、docx、txt、md 文件")
    content = await file.read()
    if len(content) > settings.upload_max_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"文件大小不能超过 {settings.upload_max_mb} MB",
        )
    import hashlib
    import uuid

    target = _kb_storage(kb_id) / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    resolved_department = department_id or kb.department_id
    resolved_visibility = visibility or ("department" if resolved_department else "public")
    document = await create_document(
        db,
        user,
        kb,
        file_path=str(target),
        filename=filename,
        file_type=suffix.lstrip("."),
        file_size=len(content),
        file_hash=hashlib.sha256(content).hexdigest(),
        title=title or filename,
        doc_no=doc_no,
        department_id=resolved_department,
        visibility=resolved_visibility,
        owner_user_id=user.id,
        review_due_date=None,
        version_label=version_label or "v1",
        effective_date=effective_date,
        ip=client_ip(request),
    )
    return document


@router.post("/kb/{kb_id}/documents/{document_id}/ingest", response_model=DocumentOut)
async def ingest_document(
    kb_id: int,
    document_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    if document.kb_id != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    version = (
        await db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文档没有可用版本")
    await parse_and_index_version(db, version)
    await publish_version(db, user, version, client_ip(request))
    _audit(db, user, "ingest_document", "document", str(document.id), document.title, client_ip(request))
    await db.commit()
    await db.refresh(document)
    return document


@router.post("/kb/{kb_id}/rebuild")
async def rebuild_kb(
    kb_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    _require_kb_manage(user, kb)
    try:
        await rebuild_knowledge_base(db, kb_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"索引重建失败：{exc}")
    _bump_index_epoch(kb)
    _audit(db, user, "rebuild_kb", "knowledge_base", str(kb_id), kb.name, client_ip(request))
    await db.commit()
    return {"ok": True}


@router.delete("/kb/{kb_id}/documents/{document_id}")
async def delete_document(
    kb_id: int,
    document_id: int,
    request: Request,
    user: User = Depends(require_document_manager),
    db: AsyncSession = Depends(get_db),
):
    document = await get_document(db, document_id, user, manage=True)
    if document.kb_id != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    await remove_document(db, user, document, client_ip(request))
    return {"ok": True}
