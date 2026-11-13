import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import client_ip, get_current_user, require_admin
from src.core.config import settings
from src.db.models import AuditLog, Conversation, Document, KnowledgeBase, User
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

router = APIRouter(tags=["知识库"])


def _audit(db: AsyncSession, admin: User, action: str, target_id: str, detail: str, ip: str) -> None:
    db.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action=action,
            target_type="knowledge_base",
            target_id=target_id,
            detail=detail,
            ip=ip,
        )
    )


def _kb_storage(kb_id: int) -> Path:
    path = settings.storage_path / f"kb_{kb_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/kb", response_model=list[KbOut])
async def list_knowledge_bases(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    statement = select(KnowledgeBase)
    if user.role != "admin":
        statement = statement.where(KnowledgeBase.status == "published")
    result = await db.execute(statement.order_by(KnowledgeBase.id))
    return result.scalars().all()


@router.post("/kb", response_model=KbOut)
async def create_knowledge_base(
    payload: KbCreateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(KnowledgeBase.id).where(KnowledgeBase.name == payload.name))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="知识库名称已存在")
    kb = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        status=payload.status,
        created_by=admin.id,
    )
    db.add(kb)
    await db.flush()
    _audit(db, admin, "create_kb", str(kb.id), kb.name, client_ip(request))
    await db.commit()
    await db.refresh(kb)
    _kb_storage(kb.id)
    return kb


@router.patch("/kb/{kb_id}", response_model=KbOut)
async def update_knowledge_base(
    kb_id: int,
    payload: KbUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    if payload.name is not None:
        duplicated = await db.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.name == payload.name, KnowledgeBase.id != kb_id)
        )
        if duplicated.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="知识库名称已存在")
        kb.name = payload.name
    if payload.description is not None:
        kb.description = payload.description
    if payload.status is not None:
        kb.status = payload.status
    _audit(db, admin, "update_kb", str(kb.id), kb.name, client_ip(request))
    await db.commit()
    await db.refresh(kb)
    return kb


@router.delete("/kb/{kb_id}")
async def delete_knowledge_base(
    kb_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")

    documents = (
        (await db.execute(select(Document).where(Document.kb_id == kb_id))).scalars().all()
    )
    for document in documents:
        await db.delete(document)
    conversations = (
        (await db.execute(select(Conversation).where(Conversation.kb_id == kb_id))).scalars().all()
    )
    for conversation in conversations:
        conversation.kb_id = None
    await db.delete(kb)
    _audit(db, admin, "delete_kb", str(kb_id), kb.name, client_ip(request))
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
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document).where(Document.kb_id == kb_id).order_by(Document.id.desc())
    )
    return result.scalars().all()


@router.post("/kb/{kb_id}/documents", response_model=DocumentOut)
async def upload_document(
    kb_id: int,
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")

    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="仅支持 pdf、docx、txt、md 文件",
        )

    content = await file.read()
    max_bytes = settings.upload_max_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"文件大小不能超过 {settings.upload_max_mb} MB",
        )

    target = _kb_storage(kb_id) / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)

    document = Document(
        kb_id=kb_id,
        filename=filename,
        stored_path=str(target),
        file_type=suffix.lstrip("."),
        size_bytes=len(content),
        status="pending",
        uploaded_by=admin.id,
    )
    db.add(document)
    await db.flush()
    _audit(db, admin, "upload_document", str(document.id), filename, client_ip(request))
    await db.commit()
    await db.refresh(document)
    return document


@router.post("/kb/{kb_id}/documents/{document_id}/ingest", response_model=DocumentOut)
async def ingest_document(
    kb_id: int,
    document_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    document = await db.get(Document, document_id)
    if document is None or document.kb_id != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    try:
        await rebuild_knowledge_base(db, kb_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"索引重建失败：{exc}")
    await db.refresh(document)
    return document


@router.post("/kb/{kb_id}/rebuild")
async def rebuild_kb(
    kb_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    try:
        await rebuild_knowledge_base(db, kb_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"索引重建失败：{exc}")
    _audit(db, admin, "rebuild_kb", str(kb_id), kb.name, client_ip(request))
    await db.commit()
    return {"ok": True}


@router.delete("/kb/{kb_id}/documents/{document_id}")
async def delete_document(
    kb_id: int,
    document_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    document = await db.get(Document, document_id)
    if document is None or document.kb_id != kb_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    stored = Path(document.stored_path)
    filename = document.filename
    await db.delete(document)
    _audit(db, admin, "delete_document", str(document_id), filename, client_ip(request))
    await db.commit()
    if stored.exists():
        stored.unlink()
    try:
        await rebuild_knowledge_base(db, kb_id)
    except Exception:
        pass
    return {"ok": True}
