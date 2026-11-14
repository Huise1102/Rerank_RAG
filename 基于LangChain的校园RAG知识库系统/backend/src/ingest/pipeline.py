from sqlalchemy.ext.asyncio import AsyncSession

from src.retrieval import bm25
from src.services.ingest_service import rebuild_kb_with_versions


async def rebuild_knowledge_base(db: AsyncSession, kb_id: int) -> dict:
    """按当前版本重建知识库索引，供分块策略变更等场景使用。"""

    return await rebuild_kb_with_versions(db, kb_id)


def reload_retrievers(kb_id: int) -> None:
    bm25.invalidate(kb_id)
