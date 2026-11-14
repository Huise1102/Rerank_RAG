from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_owned_conversation
from src.db.models import Conversation, Message, User
from src.db.session import get_db
from src.schemas.chat import (
    ChatRequest,
    ConversationDetail,
    ConversationOut,
    MessageOut,
    RetrievalDetailOut,
)
from src.services.agent_service import stream_deep_answer
from src.services.agent_settings import deep_allowed, get_agent_settings
from src.services.cache_service import get_cache_settings
from src.services.chat_service import prepare_chat, prepare_deep_chat, stream_answer

router = APIRouter(tags=["问答"])


@router.get("/chat/capabilities")
async def chat_capabilities(
    kb_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent_settings = await get_agent_settings(db)
    cache_settings = await get_cache_settings(db)
    deep_enabled = kb_id is not None and deep_allowed(agent_settings, user, kb_id)
    return {
        "deep_enabled": deep_enabled,
        "cache_enabled": bool(cache_settings.get("enabled")),
        "max_rounds": int(agent_settings.get("max_rounds", 3)),
        "role": user.role,
    }


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.app_key == "admin" and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限访问")
    if payload.mode == "deep":
        context = await prepare_deep_chat(db, user, payload)
        generator = stream_deep_answer(context, user)
    else:
        context = await prepare_chat(db, user, payload)
        generator = stream_answer(context, user)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    app_key: str | None = Query(default=None, pattern="^(admin|user)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    statement = select(Conversation).where(Conversation.user_id == user.id)
    if app_key:
        statement = statement.where(Conversation.app_key == app_key)
    result = await db.execute(statement.order_by(Conversation.updated_at.desc()).limit(100))
    return result.scalars().all()


@router.post("/conversations", response_model=ConversationOut)
async def create_conversation(
    app_key: str = Query(default="user", pattern="^(admin|user)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if app_key == "admin" and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权限访问")
    conversation = Conversation(user_id=user.id, app_key=app_key, title="新会话")
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: int,
    title: str = Query(min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await get_owned_conversation(db, conversation_id, user)
    conversation.title = title
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await get_owned_conversation(db, conversation_id, user)
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    await db.delete(conversation)
    await db.commit()
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationDetail)
async def conversation_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await get_owned_conversation(db, conversation_id, user)
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    )
    messages = result.scalars().all()
    return ConversationDetail(
        conversation=ConversationOut.model_validate(conversation),
        messages=[MessageOut.model_validate(item) for item in messages],
    )


@router.get("/messages/{message_id}/retrieval-detail", response_model=RetrievalDetailOut)
async def message_retrieval_detail(
    message_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="消息不存在")
    conversation = await db.get(Conversation, message.conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="消息不存在")
    return RetrievalDetailOut(message_id=message.id, detail=message.retrieval_detail)
