"""文档业务逻辑（document_service.py）。

上传只允许 pdf / docx / txt / md 四种格式，落库后状态为 pending，
真正的解析入库由 Celery 任务（src/tasks/document_tasks.py）异步完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.document import DOC_STATUSES, FILE_TYPES, Document
from src.db.models.knowledge_base import KnowledgeBase
from src.utils.storage import build_key, get_storage

logger = get_logger(__name__)


def detect_file_type(filename: str) -> str:
    """按扩展名判定文件类型；不支持的类型返回空串。"""
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext if ext in FILE_TYPES else ""


async def upload_document(
    db: AsyncSession,
    kb_id: int,
    *,
    filename: str,
    content: bytes,
) -> Document:
    """保存上传文件并登记文档记录（状态 pending）。

    :raises ValueError: 文件类型不支持；:raises LookupError: 知识库不存在
    """
    file_type = detect_file_type(filename)
    if not file_type:
        raise ValueError(f"仅支持 {' / '.join(FILE_TYPES)} 四种格式：{filename}")

    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise LookupError(f"知识库 {kb_id} 不存在")

    key = build_key(kb_id, filename)
    get_storage().save_bytes(key, content)

    doc = Document(
        kb_id=kb_id,
        filename=Path(filename).name,
        file_path=key,
        file_size=len(content),
        file_type=file_type,
        status="pending",
        chunk_count=0,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info("上传文档 id=%s kb=%s name=%s size=%d", doc.id, kb_id, doc.filename, len(content))
    return doc


async def list_documents(db: AsyncSession, kb_id: int) -> list[Document]:
    """列出知识库下的全部文档（按 id 倒序）。"""
    rows = await db.execute(
        select(Document).where(Document.kb_id == kb_id).order_by(Document.id.desc())
    )
    return list(rows.scalars().all())


async def get_document(db: AsyncSession, doc_id: int) -> Document | None:
    """按 id 获取文档。"""
    return await db.get(Document, doc_id)


async def delete_document(db: AsyncSession, doc_id: int) -> bool:
    """删除文档：原始文件 + 索引中的对应分块 + 数据库记录。"""
    doc = await db.get(Document, doc_id)
    if doc is None:
        return False

    kb = await db.get(KnowledgeBase, doc.kb_id)
    strategy = kb.chunk_strategy if kb else "recursive"

    # 1) 删除已上传的原始文件
    try:
        get_storage().delete(doc.file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除原始文件失败 key=%s: %s", doc.file_path, exc)

    # 2) 从索引中摘除该来源的分块并重建
    from src.services.ingest_service import remove_source

    try:
        remove_source(doc.kb_id, doc.filename, strategy)
    except Exception as exc:  # noqa: BLE001 - 索引清理失败也要删库记录
        logger.warning("移除索引分块失败 kb=%s file=%s: %s", doc.kb_id, doc.filename, exc)

    # 3) 删除数据库记录
    await db.delete(doc)
    await db.commit()
    logger.info("删除文档 id=%s kb=%s name=%s", doc_id, doc.kb_id, doc.filename)
    return True


def is_valid_status(status: str) -> bool:
    return status in DOC_STATUSES


def serialize_doc_summary(doc: Document) -> dict[str, Any]:
    """轻量摘要（供日志/调试）。"""
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
    }
