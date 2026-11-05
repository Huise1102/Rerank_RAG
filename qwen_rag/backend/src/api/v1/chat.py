"""会话管理路由（chat.py）：会话列表、消息读取、删除会话。

问答统一走 ``/agent/chat``（见 agent.py），固定管道版 ``/chat``、``/chat/stream`` 已下线。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schemas.chat import ConversationOut, MessageOut
from src.services import chat_service

router = APIRouter(tags=["会话"])


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


@router.delete("/conversations", summary="清空会话（可按知识库过滤）")
async def delete_conversations(
    kb_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    deleted = await chat_service.delete_conversations(db, kb_id)
    return {"deleted": deleted, "kb_id": kb_id}
