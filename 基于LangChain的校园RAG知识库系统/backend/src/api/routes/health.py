import chromadb
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import Document, DocumentVersion
from src.db.session import get_db
from src.retrieval import bm25
from src.retrieval.vector_store import collection_name

router = APIRouter(tags=["健康检查"])


async def _index_health(db: AsyncSession) -> dict:
    kb_rows = (
        await db.execute(select(Document.kb_id, func.count(Document.id)).group_by(Document.kb_id))
    ).all()
    client = None
    details: list[dict] = []
    mismatches = 0
    for kb_id, _ in kb_rows:
        db_chunks = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(DocumentVersion.chunk_count), 0))
                    .join(Document, Document.current_version_id == DocumentVersion.id)
                    .where(Document.kb_id == kb_id)
                )
            ).scalar_one()
        )
        chroma_chunks = 0
        try:
            client = client or chromadb.PersistentClient(path=str(settings.chroma_path))
            payload = client.get_collection(collection_name(kb_id)).get(
                where={"lifecycle_status": "published"}, include=["metadatas"]
            )
            chroma_chunks = len(payload.get("ids", []))
        except Exception:
            chroma_chunks = 0
        bm25_chunks = bm25.index_stats(kb_id)["published_chunks"]
        consistent = db_chunks == chroma_chunks == bm25_chunks
        if not consistent:
            mismatches += 1
        details.append(
            {
                "kb_id": kb_id,
                "db_chunks": db_chunks,
                "chroma_chunks": chroma_chunks,
                "bm25_chunks": bm25_chunks,
                "consistent": consistent,
            }
        )
    return {"mismatches": mismatches, "knowledge_bases": details}


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    database = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    index = {"mismatches": 0, "knowledge_bases": []}
    if database == "ok":
        try:
            index = await _index_health(db)
        except Exception:
            index = {"mismatches": 1, "knowledge_bases": [], "error": "index check failed"}
    status = "ok" if database == "ok" and index["mismatches"] == 0 else "degraded"
    return {
        "status": status,
        "database": database,
        "index": index,
        "service": "campus-rag-backend",
    }
