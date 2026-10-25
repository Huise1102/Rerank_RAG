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
    return await db.get(Document, doc_id)


def read_document_bytes(doc: Document) -> bytes:
    """读取文档原始内容，供在线预览 / 下载。

    上传的文档落在 storage/（或 MinIO），而预置语料是只读引用 product/resource/ 下的文件
    （``file_path`` 为绝对路径），两种形态都要支持。

    :raises FileNotFoundError: 原始文件已不存在或丢失
    """
    path = Path(doc.file_path)
    if path.is_absolute():
        if not path.exists():
            raise FileNotFoundError(f"原始文件不存在: {path}")
        return path.read_bytes()
    return get_storage().read_bytes(doc.file_path)


async def delete_document(db: AsyncSession, doc_id: int) -> dict[str, Any] | None:
    """删除数据库记录并立即返回；原始文件与索引分块的后台清理由调用方调度。

    索引清理（Chroma 删集合 + 全量重嵌入 + BM25 重建）是分钟级重活，若放在请求路径里
    会占死事件循环，导致 /health 等所有接口挂起、前端表现为"后端失联"。故这里只做
    毫秒级的 DB 删除，返回清理所需信息，由后台任务在线程池内执行。

    :returns: ``{doc_id, kb_id, file_path, strategy, filename}``；文档不存在时返回 ``None``
    """
    doc = await db.get(Document, doc_id)
    if doc is None:
        return None

    kb = await db.get(KnowledgeBase, doc.kb_id)
    info: dict[str, Any] = {
        "doc_id": doc.id,
        "kb_id": doc.kb_id,
        "file_path": doc.file_path,
        "strategy": kb.chunk_strategy if kb else "recursive",
        "filename": doc.filename,
    }
    await db.delete(doc)
    await db.commit()
    logger.info("删除文档记录 id=%s kb=%s name=%s（索引清理转入后台）", doc_id, info["kb_id"], info["filename"])
    return info


def cleanup_document_artifacts(kb_id: int, file_path: str, strategy: str, filename: str) -> None:
    """后台清理被删文档的原始文件与索引分块（同步函数，供线程池/后台任务调用）。

    分块的 source 记录的是**入库时实际读取的文件名**：上传文件在存储里带哈希前缀
    （如 ``kb_1/b5df89d7_报告.pdf -> source="b5df89d7_报告.pdf"``），与 ``filename`` 不同，
    用 filename 过滤会一块都匹配不到、留下孤儿分块，故必须用 ``Path(file_path).name``。
    """
    import time

    from src.services.ingest_service import remove_source

    started = time.perf_counter()
    try:
        get_storage().delete(file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除原始文件失败 key=%s: %s", file_path, exc)

    try:
        removed = remove_source(kb_id, Path(file_path).name, strategy)
        logger.info(
            "索引清理完成 kb=%s name=%s removed=%d 耗时=%.1fs",
            kb_id, filename, removed, time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响已删除的库记录
        logger.warning("移除索引分块失败 kb=%s file=%s: %s", kb_id, file_path, exc)


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
