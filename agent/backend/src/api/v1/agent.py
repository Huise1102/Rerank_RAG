"""Agent 智能问答路由（agent.py）：非流式 /agent/chat 与流式 /agent/chat/stream。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.session import get_db
from src.schemas.agent import AgentChatRequest, AgentChatResponse
from src.services import agent_service

logger = get_logger(__name__)

router = APIRouter(tags=["Agent 问答"])


async def _resolve_kb(db: AsyncSession, kb_id: int):
    from src.db.models.knowledge_base import KnowledgeBase

    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    return kb


@router.post("/agent/chat", response_model=AgentChatResponse, summary="Agent 问答（非流式）")
async def agent_chat(payload: AgentChatRequest, db: AsyncSession = Depends(get_db)):
    kb = await _resolve_kb(db, payload.kb_id)
    return await agent_service.agent_chat(
        db,
        kb,
        payload.question,
        conversation_id=payload.conversation_id,
        image=payload.image,
    )


@router.post("/agent/chat/stream", summary="Agent 问答（流式 SSE，含 agent_step 事件）")
async def agent_chat_stream(payload: AgentChatRequest, db: AsyncSession = Depends(get_db)):
    kb = await _resolve_kb(db, payload.kb_id)
    bundle = await agent_service.prepare_agent_stream(
        db,
        kb,
        payload.question,
        conversation_id=payload.conversation_id,
        image=payload.image,
    )
    return StreamingResponse(
        agent_service.agent_stream_events(bundle),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )