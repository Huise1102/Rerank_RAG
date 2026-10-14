"""对话路由（chat.py）：非流式 /chat 与流式 /chat/stream，以及会话管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.session import get_db
from src.schemas.chat import ChatRequest, ChatResponse, ConversationOut, MessageOut
from src.services import chat_service

logger = get_logger(__name__)

router = APIRouter(tags=["对话"])


async def _resolve_kb(db: AsyncSession, kb_id: int):
    from src.db.models.knowledge_base import KnowledgeBase

    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    return kb


@router.post("/chat", response_model=ChatResponse, summary="问答（非流式）")
async def chat(payload: ChatRequest, db: AsyncSession = Depends(get_db)):
    kb = await _resolve_kb(db, payload.kb_id)
    data = await chat_service.chat(
        db,
        kb,
        payload.question,
        conversation_id=payload.conversation_id,
        strategies=payload.strategy,
        image=payload.image,
    )
    return data


@router.post("/chat/stream", summary="问答（流式 SSE）")
async def chat_stream(payload: ChatRequest, db: AsyncSession = Depends(get_db)):
    kb = await _resolve_kb(db, payload.kb_id)
    bundle = await chat_service.prepare_stream(
        db,
        kb,
        payload.question,
        conversation_id=payload.conversation_id,
        strategies=payload.strategy,
        image=payload.image,
    )
    return StreamingResponse(
        chat_service.stream_events(bundle),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationOut], summary="会话列表")
async def list_conversations(
    kb_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.list_conversations(db, kb_id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="会话消息",
)
async def list_messages(conversation_id: int, db: AsyncSession = Depends(get_db)):
    conv = await chat_service.get_conversation(db, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
    return await chat_service.list_messages(db, conversation_id)


@router.delete("/conversations/{conversation_id}", summary="删除会话")
async def delete_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    ok = await chat_service.delete_conversation(db, conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
    return {"deleted": True, "conversation_id": conversation_id}
