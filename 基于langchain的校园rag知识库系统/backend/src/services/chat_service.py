import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import HTTPException, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import (
    Conversation,
    KnowledgeBase,
    Message,
    RetrievalLog,
    StrategyPreset,
    User,
    utcnow,
)
from src.db.session import async_session_factory
from src.generation.chain import build_chain, build_citations, format_context
from src.generation.llm import build_chat_model, llm_available
from src.retrieval.retrievers import build_retriever
from src.schemas.chat import ChatRequest
from src.services.setting_service import get_model_config

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    conversation_id: int
    user_message_id: int
    kb: KnowledgeBase
    strategy: StrategyPreset | None
    strategy_name: str
    documents: list
    citations: list[dict]
    detail: dict
    history: list[BaseMessage]
    question: str
    model_config: dict


def _sse(event: str, data: dict) -> str:
    payload = {"event": event, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _resolve_strategy(db: AsyncSession, strategy_id: int | None, is_admin: bool) -> StrategyPreset | None:
    if strategy_id is not None:
        preset = await db.get(StrategyPreset, strategy_id)
        if preset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="策略不存在")
        if not is_admin and not preset.is_open:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该策略未开放")
        return preset
    result = await db.execute(
        select(StrategyPreset).where(StrategyPreset.is_default.is_(True)).limit(1)
    )
    preset = result.scalar_one_or_none()
    if preset is None:
        result = await db.execute(
            select(StrategyPreset).where(StrategyPreset.is_open.is_(True)).order_by(StrategyPreset.id).limit(1)
        )
        preset = result.scalar_one_or_none()
    if preset and not is_admin and not preset.is_open:
        return None
    return preset


async def _load_history(db: AsyncSession, conversation_id: int, exclude_message_id: int) -> list[BaseMessage]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.id != exclude_message_id)
        .order_by(Message.id.desc())
        .limit(settings.history_max_turns * 2)
    )
    messages = list(reversed(result.scalars().all()))
    history: list[BaseMessage] = []
    for item in messages:
        if item.role == "user":
            history.append(HumanMessage(content=item.content))
        elif item.role == "assistant":
            history.append(AIMessage(content=item.content))
    return history


async def prepare_chat(db: AsyncSession, user: User, payload: ChatRequest) -> ChatContext:
    is_admin = user.role == "admin"
    model_config = await get_model_config(db)
    if not llm_available(model_config):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="尚未配置大模型密钥，请在项目根 .env 中设置 LLM_API_KEY",
        )

    kb = await db.get(KnowledgeBase, payload.kb_id)
    if kb is None or (not is_admin and kb.status != "published"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在或未发布")

    preset = await _resolve_strategy(db, payload.strategy_id, is_admin)

    if payload.conversation_id is not None:
        conversation = await db.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
        conversation.kb_id = kb.id
    else:
        title = payload.question.strip()[:30] or "新会话"
        conversation = Conversation(
            user_id=user.id, kb_id=kb.id, app_key=payload.app_key, title=title
        )
        db.add(conversation)
        await db.flush()

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.question,
        kb_id=kb.id,
        strategy_id=preset.id if preset else None,
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(conversation)

    started = time.perf_counter()
    bundle = build_retriever(kb.id, preset)
    try:
        documents = await bundle.retriever.ainvoke(payload.question)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    duration_ms = int((time.perf_counter() - started) * 1000)

    citations = build_citations(documents)
    vector_hits = len(bundle.vector_tracker.bucket) if bundle.vector_tracker else 0
    bm25_hits = len(bundle.bm25_tracker.bucket) if bundle.bm25_tracker else 0
    detail = {
        "strategy": preset.name if preset else "系统默认",
        "rerank": bundle.used_rerank,
        "vector_hits": vector_hits,
        "bm25_hits": bm25_hits,
        "fused_hits": max(vector_hits, bm25_hits),
        "reranked_hits": len(documents),
        "duration_ms": duration_ms,
        "params": bundle.fields,
        "documents": [
            {
                "index": index,
                "filename": item.metadata.get("filename", ""),
                "page": item.metadata.get("page"),
                "document_id": item.metadata.get("document_id"),
                "preview": item.page_content.strip()[:160],
            }
            for index, item in enumerate(documents, start=1)
        ],
    }

    history = await _load_history(db, conversation.id, user_message.id)
    return ChatContext(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        kb=kb,
        strategy=preset,
        strategy_name=preset.name if preset else "系统默认",
        documents=documents,
        citations=citations,
        detail=detail,
        history=history,
        question=payload.question,
        model_config=model_config,
    )


async def _persist_answer(
    context: ChatContext, user_id: int, answer: str, failed: str | None
) -> int | None:
    async with async_session_factory() as db:
        message = Message(
            conversation_id=context.conversation_id,
            role="assistant",
            content=answer,
            kb_id=context.kb.id,
            strategy_id=context.strategy.id if context.strategy else None,
            citations=context.citations,
            retrieval_detail=context.detail,
        )
        db.add(message)
        await db.flush()
        conversation = await db.get(Conversation, context.conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()
        db.add(
            RetrievalLog(
                user_id=user_id,
                kb_id=context.kb.id,
                conversation_id=context.conversation_id,
                message_id=message.id,
                strategy_id=context.strategy.id if context.strategy else None,
                strategy_name=context.strategy_name,
                question=context.question[:500],
                vector_hits=context.detail.get("vector_hits", 0),
                bm25_hits=context.detail.get("bm25_hits", 0),
                fused_hits=context.detail.get("fused_hits", 0),
                reranked_hits=context.detail.get("reranked_hits", 0),
                duration_ms=context.detail.get("duration_ms", 0),
                citations=context.citations,
            )
        )
        await db.commit()
        await db.refresh(message)
        if failed:
            logger.warning("回答生成中断：%s", failed)
        return message.id


async def stream_answer(context: ChatContext, user: User) -> AsyncIterator[str]:
    yield _sse(
        "meta",
        {
            "conversation_id": context.conversation_id,
            "user_message_id": context.user_message_id,
            "strategy": context.strategy_name,
        },
    )
    yield _sse("citations", {"citations": context.citations})

    chain = build_chain(build_chat_model(context.model_config))
    parts: list[str] = []
    failure: str | None = None
    try:
        async for chunk in chain.astream(
            {
                "context": format_context(context.documents),
                "question": context.question,
                "history": context.history,
            }
        ):
            text = str(chunk)
            if not text:
                continue
            parts.append(text)
            yield _sse("delta", {"content": text})
    except Exception as exc:
        failure = str(exc)
        yield _sse("error", {"detail": "回答生成失败，请稍后重试"})

    answer = "".join(parts).strip()
    if not answer:
        answer = "未找到相关内容。"
    message_id = await _persist_answer(context, user.id, answer, failure)
    yield _sse("done", {"message_id": message_id, "citations": context.citations})
