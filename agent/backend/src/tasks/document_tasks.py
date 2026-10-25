"""文档解析入库 Celery 任务（document_tasks.py）。

任务参数只用基本类型（doc_id），不传 ORM 对象。任务体是同步函数：内部用
``asyncio.run`` 跑一段 async 逻辑（读写数据库），中间的解析/分块/索引是阻塞计算，
在 worker 进程里顺序执行不会阻塞任何事件循环。

``asyncio.run`` 每次都新建事件循环，因此数据库会话必须走 ``TaskSessionLocal``
（NullPool 引擎，不跨循环复用连接），不能复用请求路径的连接池。

状态流转：pending -> parsing -> indexing -> completed / failed。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.db.models.document import Document
from src.db.models.knowledge_base import KnowledgeBase
from src.db.session import TaskSessionLocal
from src.services import ingest_service
from src.tasks.celery_app import celery_app
from src.utils.storage import get_storage

logger = get_logger(__name__)


async def _set_status(doc_id: int, status: str, *, error: str | None = None,
                      chunk_count: int | None = None) -> None:
    async with TaskSessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            return
        doc.status = status
        if error is not None:
            doc.error_msg = error[:2000]
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        await db.commit()


async def _process(doc_id: int) -> dict[str, Any]:
    async with TaskSessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            return {"doc_id": doc_id, "status": "missing", "error": "文档不存在"}
        kb = await db.get(KnowledgeBase, doc.kb_id)
        if kb is None:
            return {"doc_id": doc_id, "status": "missing", "error": "知识库不存在"}
        kb_id, strategy, file_path, filename = doc.kb_id, kb.chunk_strategy, doc.file_path, doc.filename

    await _set_status(doc_id, "parsing")

    storage = get_storage()
    local_path = storage.materialize(file_path)
    temp_file: Path | None = local_path if storage.__class__.__name__ == "MinioStorage" else None

    try:
        await _set_status(doc_id, "indexing")
        result = ingest_service.ingest_document(kb_id, local_path, strategy)
        added = int(result.get("added_chunks", 0))
        await _set_status(doc_id, "completed", chunk_count=added, error=None)
        logger.info(
            "文档入库完成 doc=%s kb=%s file=%s added=%d total=%d",
            doc_id, kb_id, filename, added, result.get("total_chunks"),
        )
        return {"doc_id": doc_id, "status": "completed", **result}
    except Exception as exc:  # noqa: BLE001 - 任何异常都要落到 failed 状态
        logger.exception("文档入库失败 doc=%s: %s", doc_id, exc)
        await _set_status(doc_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return {"doc_id": doc_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if temp_file is not None and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


@celery_app.task(name="documents.parse_and_index", bind=True, max_retries=0)
def parse_and_index_document(self, doc_id: int) -> dict[str, Any]:
    """解析并索引单个文档（同步入口，Celery worker 调用）。"""
    return asyncio.run(_process(doc_id))
