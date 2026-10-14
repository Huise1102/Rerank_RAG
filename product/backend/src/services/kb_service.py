"""知识库业务逻辑（kb_service.py）。

无用户体系：知识库对所有人可见。删除知识库会级联删除其文档、会话，
并清理对应的向量索引、BM25 索引、分块文件与已上传的原始文件。
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.document import Document
from src.db.models.knowledge_base import DEFAULT_STRATEGIES, KnowledgeBase
from src.schemas.kb import KBCreate, KBUpdate

logger = get_logger(__name__)


def normalize_strategies(raw: dict[str, bool] | None) -> dict[str, bool]:
    """归一化构建块开关：只保留已知键，缺省全开。"""
    out = dict(DEFAULT_STRATEGIES)
    if raw:
        for key in DEFAULT_STRATEGIES:
            if key in raw:
                out[key] = bool(raw[key])
    return out


def get_enabled_strategies(kb: KnowledgeBase) -> dict[str, bool]:
    """读取知识库的构建块开关（容错：脏数据按全开处理）。"""
    return normalize_strategies(kb.enabled_strategies)


async def create_kb(db: AsyncSession, data: KBCreate) -> KnowledgeBase:
    """新建知识库。"""
    kb = KnowledgeBase(
        name=data.name.strip(),
        description=(data.description or None),
        chunk_strategy=data.chunk_strategy,
        enabled_strategies=normalize_strategies(data.enabled_strategies),
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    logger.info("创建知识库 id=%s name=%s", kb.id, kb.name)
    return kb


async def list_kbs(db: AsyncSession) -> list[KnowledgeBase]:
    """列出全部知识库（按创建时间倒序）。"""
    rows = await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.id.desc()))
    return list(rows.scalars().all())


async def get_kb(db: AsyncSession, kb_id: int) -> KnowledgeBase | None:
    """按 id 获取知识库。"""
    return await db.get(KnowledgeBase, kb_id)


async def update_kb(db: AsyncSession, kb_id: int, data: KBUpdate) -> KnowledgeBase | None:
    """更新知识库基本信息与开关（只更新传入字段）。"""
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        return None
    if data.name is not None:
        kb.name = data.name.strip()
    if data.description is not None:
        kb.description = data.description or None
    if data.chunk_strategy is not None:
        kb.chunk_strategy = data.chunk_strategy
    if data.enabled_strategies is not None:
        kb.enabled_strategies = normalize_strategies(data.enabled_strategies)
    await db.commit()
    await db.refresh(kb)
    return kb


async def update_strategies(
    db: AsyncSession, kb_id: int, strategies: dict[str, bool]
) -> KnowledgeBase | None:
    """单独更新构建块开关（PATCH /kb/{id}/strategies）。"""
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        return None
    kb.enabled_strategies = normalize_strategies(strategies)
    await db.commit()
    await db.refresh(kb)
    logger.info("更新检索策略 kb=%s %s", kb_id, kb.enabled_strategies)
    return kb


async def delete_kb(db: AsyncSession, kb_id: int) -> bool:
    """删除知识库及其索引、原始文件（文档/会话由外键级联删除）。"""
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        return False

    strategy = kb.chunk_strategy

    # 1) 清理已上传的原始文件
    from src.utils.storage import get_storage

    storage = get_storage()
    doc_rows = await db.execute(select(Document.file_path).where(Document.kb_id == kb_id))
    for (file_path,) in doc_rows.all():
        try:
            storage.delete(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除原始文件失败 key=%s: %s", file_path, exc)

    # 2) 清理索引与分块文件
    from src.services.ingest_service import drop_kb

    drop_kb(kb_id, strategy)

    # 3) 删除知识库记录（文档、会话随外键级联删除）
    await db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    await db.commit()
    logger.info("删除知识库 id=%s", kb_id)
    return True
