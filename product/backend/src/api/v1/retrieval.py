"""检索调试路由（retrieval.py）：只检索不生成，返回结果与耗时。

检索是阻塞的 CPU 操作，在 ``run_in_threadpool`` 中执行，避免阻塞事件循环。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.retrieval_log import RetrievalLog
from src.db.session import get_db
from src.services.kb_service import get_enabled_strategies
from src.services.retrieval_service import retrieve

logger = get_logger(__name__)

router = APIRouter(tags=["检索"])


class RetrievalDebugRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=200)
    strategy: dict[str, bool] | None = None


@router.post("/kb/{kb_id}/retrieve", summary="检索调试（只检索不生成）")
async def retrieve_debug(
    kb_id: int, payload: RetrievalDebugRequest, db: AsyncSession = Depends(get_db)
):
    from src.db.models.knowledge_base import KnowledgeBase

    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    strategies = payload.strategy or get_enabled_strategies(kb)
    resp = await run_in_threadpool(retrieve, kb_id, payload.query, strategies, payload.top_k)

    # 落一条检索日志（失败不阻断返回）
    try:
        db.add(
            RetrievalLog(
                kb_id=kb_id,
                query=resp.query,
                strategy=resp.strategy,
                top_k=(resp.params or {}).get("top_k"),
                latency=round(float((resp.timing or {}).get("total", 0.0)), 4),
                result_count=len(resp.results),
            )
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning("写检索日志失败: %s", exc)

    data = resp.to_dict(include_text=True)
    data["latency"] = {
        "recall_ms": round(float(resp.timing.get("recall", 0.0)) * 1000),
        "retrieve_ms": round(float(resp.timing.get("total", 0.0)) * 1000),
        "rerank_ms": round(float(resp.timing.get("rerank", 0.0)) * 1000),
    }
    return data
