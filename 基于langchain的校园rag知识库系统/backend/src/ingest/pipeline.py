import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Document
from src.ingest.loaders import load_document
from src.ingest.splitter import split_documents
from src.retrieval import bm25
from src.retrieval.vector_store import delete_collection, get_vector_store

logger = logging.getLogger(__name__)

BATCH_SIZE = 64


def _build_sync(kb_id: int, entries: list[tuple[int, str, str]]) -> dict[int, tuple[int, str | None]]:
    results: dict[int, tuple[int, str | None]] = {}
    all_chunks = []
    for document_id, stored_path, filename in entries:
        try:
            documents = load_document(Path(stored_path), kb_id, document_id, filename)
            chunks = split_documents(documents)
            results[document_id] = (len(chunks), None)
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.exception("文档解析失败 %s", filename)
            results[document_id] = (0, str(exc))

    delete_collection(kb_id)
    if all_chunks:
        store = get_vector_store(kb_id)
        for start in range(0, len(all_chunks), BATCH_SIZE):
            store.add_documents(all_chunks[start : start + BATCH_SIZE])
    bm25.save_documents(kb_id, all_chunks)
    bm25.invalidate(kb_id)
    return results


async def rebuild_knowledge_base(db: AsyncSession, kb_id: int) -> None:
    documents = (
        (await db.execute(select(Document).where(Document.kb_id == kb_id).order_by(Document.id)))
        .scalars()
        .all()
    )
    entries = [(item.id, item.stored_path, item.filename) for item in documents]
    results = await asyncio.to_thread(_build_sync, kb_id, entries)
    for item in documents:
        chunk_count, error = results.get(item.id, (0, "文档未参与索引"))
        if error:
            item.status = "failed"
            item.error_message = error[:500]
            item.chunk_count = 0
        else:
            item.status = "ready"
            item.error_message = None
            item.chunk_count = chunk_count
    await db.commit()


def reload_retrievers(kb_id: int) -> None:
    bm25.invalidate(kb_id)
