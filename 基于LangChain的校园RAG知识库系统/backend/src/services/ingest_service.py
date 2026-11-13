import asyncio
import logging
from datetime import date
from pathlib import Path

from langchain_core.documents import Document as LangDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import Department, Document, DocumentVersion, KnowledgeBase
from src.ingest.loaders import load_document_with_report
from src.ingest.splitter import split_documents
from src.retrieval import bm25
from src.retrieval.vector_store import get_vector_store

logger = logging.getLogger(__name__)
BATCH_SIZE = 64
NEVER_REPEALED = "9999-12-31T23:59:59"
NEVER_REPEALED_TS = 99991231235959


def _date_ts(value: str) -> int:
    digits = value[:10].replace("-", "")
    return int(digits) if digits.isdigit() else 0


def _repealed_ts(value: str) -> int:
    if value.startswith("9999"):
        return NEVER_REPEALED_TS
    digits = "".join(character for character in value if character.isdigit())[:14]
    return int(digits) if digits else NEVER_REPEALED_TS


def _sanitize_metadata(metadata: dict) -> dict:
    cleaned: dict = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


def build_chunk_metadata(
    document: Document,
    version: DocumentVersion,
    department_name: str = "",
) -> dict:
    effective = version.effective_date or date.today()
    repealed = version.repealed_at.isoformat(timespec="seconds") if version.repealed_at else NEVER_REPEALED
    return {
        "kb_id": int(document.kb_id),
        "document_id": int(document.id),
        "version_id": int(version.id),
        "version_label": version.version_label,
        "source": document.filename,
        "filename": document.filename,
        "doc_title": document.title or document.filename,
        "doc_no": document.doc_no or "",
        "department_id": int(document.department_id or 0),
        "department_name": department_name,
        "visibility": document.visibility,
        "lifecycle_status": version.lifecycle_status,
        "effective_date": effective.isoformat(),
        "effective_ts": _date_ts(effective.isoformat()),
        "repealed_at": repealed,
        "repealed_ts": _repealed_ts(repealed),
    }


def _collect_store_documents(store) -> list[LangDocument]:
    payload = store.get(include=["documents", "metadatas"])
    documents: list[LangDocument] = []
    for text, metadata in zip(payload.get("documents", []), payload.get("metadatas", [])):
        if text:
            documents.append(LangDocument(page_content=text, metadata=dict(metadata or {})))
    return documents


def _replace_version_chunks(store, version_id: int, chunks: list[LangDocument]) -> None:
    existing = store.get(where={"version_id": int(version_id)}, include=["metadatas"])
    ids = existing.get("ids", [])
    if ids:
        store.delete(ids=ids)
    for start in range(0, len(chunks), BATCH_SIZE):
        store.add_documents(chunks[start : start + BATCH_SIZE])


def _index_version_sync(
    document_data: dict,
    version_data: dict,
    strategy: str,
    department_name: str,
) -> dict:
    path = Path(version_data["file_path"])
    documents, warnings = load_document_with_report(
        path,
        document_data["kb_id"],
        document_data["id"],
        document_data["filename"],
    )
    doc_meta = {
        "document_id": document_data["id"],
        "version_id": version_data["id"],
        "kb_id": document_data["kb_id"],
        "doc_no": document_data.get("doc_no") or "",
        "doc_title": document_data.get("title") or document_data["filename"],
        "version_label": version_data["version_label"],
        "department_id": document_data.get("department_id") or 0,
        "department_name": department_name,
        "visibility": document_data["visibility"],
        "lifecycle_status": version_data["lifecycle_status"],
        "effective_date": version_data["effective_date"],
        "source": document_data["filename"],
    }
    chunks = split_documents(documents, strategy=strategy, doc_meta=doc_meta)
    for chunk in chunks:
        base = {
            "kb_id": document_data["kb_id"],
            "document_id": document_data["id"],
            "version_id": version_data["id"],
            "version_label": version_data["version_label"],
            "source": document_data["filename"],
            "filename": document_data["filename"],
            "doc_title": document_data.get("title") or document_data["filename"],
            "doc_no": document_data.get("doc_no") or "",
            "department_id": document_data.get("department_id") or 0,
            "department_name": department_name,
            "visibility": document_data["visibility"],
            "lifecycle_status": version_data["lifecycle_status"],
            "effective_date": version_data["effective_date"],
            "effective_ts": _date_ts(version_data["effective_date"]),
            "repealed_at": version_data["repealed_at"],
            "repealed_ts": _repealed_ts(version_data["repealed_at"]),
        }
        chunk.metadata.update(base)
        chunk.metadata = _sanitize_metadata(chunk.metadata)

    store = get_vector_store(document_data["kb_id"])
    _replace_version_chunks(store, version_data["id"], chunks)
    bm25.save_documents(document_data["kb_id"], _collect_store_documents(store))
    bm25.invalidate(document_data["kb_id"])

    report = {
        "files": 1,
        "chunks": len(chunks),
        "tables": sum(1 for item in chunks if item.metadata.get("chunk_type") == "table"),
        "clauses": sum(
            1 for item in chunks if item.metadata.get("chunk_type") == "clause"
        ),
        "warnings": warnings,
        "strategy": strategy,
    }
    return report


def _version_payload(version: DocumentVersion) -> dict:
    return {
        "id": version.id,
        "version_label": version.version_label,
        "file_path": version.file_path,
        "lifecycle_status": version.lifecycle_status,
        "effective_date": (version.effective_date or date.today()).isoformat(),
        "repealed_at": version.repealed_at.isoformat(timespec="seconds")
        if version.repealed_at
        else NEVER_REPEALED,
    }


async def parse_and_index_version(
    db: AsyncSession, version: DocumentVersion, strategy: str | None = None
) -> dict:
    document = await db.get(Document, version.document_id)
    if document is None:
        raise ValueError("文档不存在")
    kb = await db.get(KnowledgeBase, document.kb_id)
    if kb is None:
        raise ValueError("知识库不存在")
    department = (
        await db.get(Department, document.department_id) if document.department_id else None
    )
    strategy = strategy or kb.chunk_strategy or settings.chunk_strategy

    version.process_status = "parsing"
    version.error_msg = None
    await db.commit()

    document_data = {
        "id": document.id,
        "kb_id": document.kb_id,
        "filename": document.filename,
        "title": document.title,
        "doc_no": document.doc_no,
        "department_id": document.department_id,
        "visibility": document.visibility,
    }
    try:
        report = await asyncio.to_thread(
            _index_version_sync,
            document_data,
            _version_payload(version),
            strategy,
            department.name if department else "",
        )
    except Exception as exc:
        logger.exception("文档版本索引失败 %s", version.id)
        version.process_status = "failed"
        version.error_msg = str(exc)[:2000]
        document.status = "failed"
        document.error_message = str(exc)[:500]
        await db.commit()
        raise

    version.process_status = "completed"
    version.chunk_count = int(report["chunks"])
    version.table_count = int(report["tables"])
    version.clause_count = int(report["clauses"])
    version.ingest_report = report
    document.status = "ready"
    document.error_message = None
    document.chunk_count = int(report["chunks"])
    await db.commit()
    return report


def _refresh_metadata_sync(
    version_id: int, kb_id: int, overrides: dict
) -> int:
    store = get_vector_store(kb_id)
    payload = store.get(where={"version_id": int(version_id)}, include=["metadatas"])
    ids = payload.get("ids", [])
    metadatas = payload.get("metadatas", [])
    if ids:
        merged = [dict(metadata or {}, **overrides) for metadata in metadatas]
        store._collection.update(ids=ids, metadatas=[_sanitize_metadata(item) for item in merged])
    documents = bm25.load_documents(kb_id)
    if documents:
        for item in documents:
            if str(item.metadata.get("version_id")) == str(version_id):
                item.metadata.update(overrides)
        bm25.save_documents(kb_id, documents)
    return len(ids)


async def refresh_version_metadata(db: AsyncSession, version: DocumentVersion) -> int:
    document = await db.get(Document, version.document_id)
    if document is None:
        return 0
    department = (
        await db.get(Department, document.department_id) if document.department_id else None
    )
    metadata = build_chunk_metadata(document, version, department.name if department else "")
    return await asyncio.to_thread(
        _refresh_metadata_sync, version.id, document.kb_id, metadata
    )


async def delete_version_chunks(db: AsyncSession, version: DocumentVersion) -> None:
    document = await db.get(Document, version.document_id)
    if document is None:
        return

    def _delete() -> None:
        store = get_vector_store(document.kb_id)
        payload = store.get(where={"version_id": int(version.id)}, include=["metadatas"])
        ids = payload.get("ids", [])
        if ids:
            store.delete(ids=ids)
        bm25.save_documents(document.kb_id, _collect_store_documents(store))
        bm25.invalidate(document.kb_id)

    await asyncio.to_thread(_delete)


def _consistency_sync(kb_id: int, version_id: int, db_chunks: int) -> dict:
    store = get_vector_store(kb_id)
    payload = store.get(where={"version_id": int(version_id)}, include=["metadatas"])
    chroma_chunks = len(payload.get("ids", []))
    bm25_documents = bm25.load_documents(kb_id)
    bm25_chunks = sum(
        1 for item in bm25_documents if str(item.metadata.get("version_id")) == str(version_id)
    )
    consistent = db_chunks == chroma_chunks == bm25_chunks
    return {
        "version_id": version_id,
        "db_chunks": db_chunks,
        "chroma_chunks": chroma_chunks,
        "bm25_chunks": bm25_chunks,
        "consistent": consistent,
    }


async def check_index_consistency(db: AsyncSession, version: DocumentVersion) -> dict:
    document = await db.get(Document, version.document_id)
    if document is None:
        raise ValueError("文档不存在")
    return await asyncio.to_thread(
        _consistency_sync, document.kb_id, version.id, version.chunk_count
    )


def _rebuild_kb_sync(kb_id: int, entries: list[dict], strategy: str) -> dict:
    from src.retrieval.vector_store import delete_collection

    delete_collection(kb_id)
    store = get_vector_store(kb_id)
    all_chunks: list[LangDocument] = []
    results: dict[int, dict] = {}
    for entry in entries:
        try:
            documents, warnings = load_document_with_report(
                Path(entry["file_path"]), kb_id, entry["document_id"], entry["filename"]
            )
            doc_meta = {
                "document_id": entry["document_id"],
                "version_id": entry["version_id"],
                "kb_id": kb_id,
                "doc_no": entry["doc_no"],
                "doc_title": entry["doc_title"],
                "version_label": entry["version_label"],
                "department_id": entry["department_id"],
                "department_name": entry["department_name"],
                "visibility": entry["visibility"],
                "lifecycle_status": entry["lifecycle_status"],
                "effective_date": entry["effective_date"],
                "source": entry["filename"],
            }
            chunks = split_documents(documents, strategy=strategy, doc_meta=doc_meta)
            for chunk in chunks:
                chunk.metadata.update(
                    {
                        "kb_id": kb_id,
                        "document_id": entry["document_id"],
                        "version_id": entry["version_id"],
                        "version_label": entry["version_label"],
                        "source": entry["filename"],
                        "filename": entry["filename"],
                        "doc_title": entry["doc_title"],
                        "doc_no": entry["doc_no"],
                        "department_id": entry["department_id"],
                        "department_name": entry["department_name"],
                        "visibility": entry["visibility"],
                        "lifecycle_status": entry["lifecycle_status"],
                        "effective_date": entry["effective_date"],
                        "effective_ts": _date_ts(entry["effective_date"]),
                        "repealed_at": entry["repealed_at"],
                        "repealed_ts": _repealed_ts(entry["repealed_at"]),
                    }
                )
                chunk.metadata = _sanitize_metadata(chunk.metadata)
            results[entry["version_id"]] = {
                "chunks": len(chunks),
                "tables": sum(
                    1 for item in chunks if item.metadata.get("chunk_type") == "table"
                ),
                "clauses": sum(
                    1 for item in chunks if item.metadata.get("chunk_type") == "clause"
                ),
                "warnings": warnings,
            }
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.exception("文档重建失败 %s", entry["filename"])
            results[entry["version_id"]] = {"chunks": 0, "error": str(exc), "warnings": []}

    for start in range(0, len(all_chunks), BATCH_SIZE):
        store.add_documents(all_chunks[start : start + BATCH_SIZE])
    bm25.save_documents(kb_id, all_chunks)
    bm25.invalidate(kb_id)
    return results


async def rebuild_kb_with_versions(db: AsyncSession, kb_id: int) -> dict:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise ValueError("知识库不存在")
    rows = (
        await db.execute(
            select(Document, DocumentVersion, Department)
            .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
            .outerjoin(Department, Department.id == Document.department_id)
            .where(Document.kb_id == kb_id)
        )
    ).all()
    entries: list[dict] = []
    version_map: dict[int, DocumentVersion] = {}
    document_map: dict[int, Document] = {}
    for document, version, department in rows:
        version_map[version.id] = version
        document_map[document.id] = document
        entries.append(
            {
                "document_id": document.id,
                "version_id": version.id,
                "filename": document.filename,
                "file_path": version.file_path,
                "doc_title": document.title or document.filename,
                "doc_no": document.doc_no or "",
                "version_label": version.version_label,
                "department_id": document.department_id or 0,
                "department_name": department.name if department else "",
                "visibility": document.visibility,
                "lifecycle_status": version.lifecycle_status,
                "effective_date": (version.effective_date or date.today()).isoformat(),
                "repealed_at": version.repealed_at.isoformat(timespec="seconds")
                if version.repealed_at
                else NEVER_REPEALED,
            }
        )
    results = await asyncio.to_thread(_rebuild_kb_sync, kb_id, entries, kb.chunk_strategy)
    for version_id, result in results.items():
        version = version_map.get(version_id)
        if version is None:
            continue
        if result.get("error"):
            version.process_status = "failed"
            version.error_msg = result["error"][:2000]
            version.chunk_count = 0
        else:
            version.process_status = "completed"
            version.error_msg = None
            version.chunk_count = int(result["chunks"])
            version.table_count = int(result["tables"])
            version.clause_count = int(result["clauses"])
            version.ingest_report = result
        document = document_map.get(version.document_id)
        if document is None:
            continue
        document.status = "failed" if result.get("error") else "ready"
        document.error_message = result.get("error")
        document.chunk_count = version.chunk_count
    await db.commit()
    return results
