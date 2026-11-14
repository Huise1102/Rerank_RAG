import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import HTTPException, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models import (
    Conversation,
    Department,
    KnowledgeBase,
    Message,
    RetrievalLog,
    StrategyPreset,
    User,
    utcnow,
)
from src.db.session import async_session_factory
from src.generation.chain import (
    DISCLAIMER,
    REFUSAL,
    build_chain,
    build_citations,
    format_context,
)
from src.generation.llm import build_chat_model, llm_available
from src.retrieval.retrievers import build_retriever
from src.retrieval.scope import RetrievalScope
from src.schemas.chat import ChatRequest
from src.services.agent_settings import deep_allowed, get_agent_settings
from src.services.cache_service import lookup_cached_answer, store_cached_answer
from src.services.kb_service import can_access_kb
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
    scope: RetrievalScope
    transfer_required: bool = False
    cached_answer: str | None = None
    cache_eligible: bool = False
    agent_config: dict | None = None


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


async def _resolve_chat_kb(db: AsyncSession, kb_id: int, user: User) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在或未发布")
    department = await db.get(Department, kb.department_id) if kb.department_id else None
    if not can_access_kb(
        user.role,
        user.department_id,
        kb.visibility,
        kb.department_id,
        kb.status,
        department.status if department else None,
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="知识库不存在或未发布")
    return kb


async def prepare_chat(db: AsyncSession, user: User, payload: ChatRequest) -> ChatContext:
    is_admin = user.role == "admin"
    model_config = await get_model_config(db)
    if not llm_available(model_config):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="尚未配置大模型密钥，请在项目根 .env 中设置 LLM_API_KEY",
        )

    kb = await _resolve_chat_kb(db, payload.kb_id, user)

    preset = await _resolve_strategy(db, payload.strategy_id, is_admin)
    scope = RetrievalScope.from_user(user)
    strategy_id = preset.id if preset else None
    cache_eligible = payload.mode == "fast"
    if cache_eligible and payload.conversation_id is not None:
        existing_messages = await db.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == payload.conversation_id
            )
        )
        cache_eligible = int(existing_messages or 0) == 0
    cached = None
    if cache_eligible:
        cached = await lookup_cached_answer(db, kb, strategy_id, scope, payload.question)

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
        strategy_id=strategy_id,
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(conversation)

    history = await _load_history(db, conversation.id, user_message.id)
    if cached is not None:
        cached_detail = dict(cached.get("detail") or {})
        cached_detail["cache"] = {
            "hit": True,
            "epoch": kb.index_epoch,
            "created_at": cached.get("created_at", ""),
            "match_type": cached.get("match_type", "exact"),
            "similarity": cached.get("similarity"),
            "matched_question": cached.get("matched_question", ""),
        }
        cached_detail["strategy"] = cached.get("strategy_name") or cached_detail.get("strategy", "")
        return ChatContext(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            kb=kb,
            strategy=preset,
            strategy_name=cached.get("strategy_name") or (preset.name if preset else "系统默认"),
            documents=[],
            citations=cached.get("citations") or [],
            detail=cached_detail,
            history=history,
            question=payload.question,
            model_config=model_config,
            scope=scope,
            transfer_required=False,
            cached_answer=cached.get("answer", ""),
            cache_eligible=True,
        )

    started = time.perf_counter()
    bundle = build_retriever(kb.id, preset, scope)
    try:
        documents = await bundle.retriever.ainvoke(payload.question)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    duration_ms = int((time.perf_counter() - started) * 1000)

    citations = build_citations(documents)
    vector_hits = len(bundle.vector_tracker.bucket) if bundle.vector_tracker else 0
    bm25_hits = len(bundle.bm25_tracker.bucket) if bundle.bm25_tracker else 0
    versions = sorted(
        {
            f"{item.metadata.get('document_id')}:{item.metadata.get('version_label')}"
            for item in documents
            if item.metadata.get("document_id")
        }
    )
    detail = {
        "strategy": preset.name if preset else "系统默认",
        "rerank": bundle.used_rerank,
        "vector_hits": vector_hits,
        "bm25_hits": bm25_hits,
        "fused_hits": max(vector_hits, bm25_hits),
        "reranked_hits": len(documents),
        "duration_ms": duration_ms,
        "role": scope.role,
        "department_id": scope.department_id,
        "visibility_scope": scope.description,
        "versions": versions,
        "params": bundle.fields,
        "cache": {"hit": False, "epoch": kb.index_epoch},
        "documents": [
            {
                "index": index,
                "filename": item.metadata.get("filename", ""),
                "page": item.metadata.get("page"),
                "article_no": item.metadata.get("article_no"),
                "version_label": item.metadata.get("version_label"),
                "document_id": item.metadata.get("document_id"),
                "preview": item.page_content.strip()[:160],
            }
            for index, item in enumerate(documents, start=1)
        ],
    }

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
        scope=scope,
        transfer_required=not citations,
        cache_eligible=cache_eligible,
    )


async def prepare_deep_chat(db: AsyncSession, user: User, payload: ChatRequest) -> ChatContext:
    """准备深度问答上下文，检索交给 Agent 循环执行。"""
    agent_settings = await get_agent_settings(db)
    kb = await _resolve_chat_kb(db, payload.kb_id, user)
    if not deep_allowed(agent_settings, user, kb.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="当前知识库未开放深度问答")
    model_config = await get_model_config(db)
    if not llm_available(model_config):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="尚未配置大模型密钥，请在项目根 .env 中设置 LLM_API_KEY",
        )

    is_admin = user.role == "admin"
    preset = await _resolve_strategy(db, payload.strategy_id, is_admin)
    scope = RetrievalScope.from_user(user)

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
    history = await _load_history(db, conversation.id, user_message.id)

    detail = {
        "strategy": preset.name if preset else "系统默认",
        "mode": "deep",
        "rerank": bool(preset.rerank) if preset else settings.rerank_enabled,
        "vector_hits": 0,
        "bm25_hits": 0,
        "fused_hits": 0,
        "reranked_hits": 0,
        "duration_ms": 0,
        "role": scope.role,
        "department_id": scope.department_id,
        "visibility_scope": scope.description,
        "versions": [],
        "cache": {"hit": False, "epoch": kb.index_epoch},
        "agent_trace": [],
        "agent_rounds": 0,
        "agent_retrievals": 0,
        "documents": [],
    }
    return ChatContext(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        kb=kb,
        strategy=preset,
        strategy_name=preset.name if preset else "系统默认",
        documents=[],
        citations=[],
        detail=detail,
        history=history,
        question=payload.question,
        model_config=model_config,
        scope=scope,
        transfer_required=False,
        cache_eligible=False,
        agent_config=agent_settings,
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
                role=context.scope.role,
                department_id=context.scope.department_id,
                visibility_scope=context.scope.description,
                document_versions=context.detail.get("versions", []),
                question=context.question[:500],
                vector_hits=context.detail.get("vector_hits", 0),
                bm25_hits=context.detail.get("bm25_hits", 0),
                fused_hits=context.detail.get("fused_hits", 0),
                reranked_hits=context.detail.get("reranked_hits", 0),
                duration_ms=context.detail.get("duration_ms", 0),
                cache_hit=bool(context.detail.get("cache", {}).get("hit", False)),
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
            "transfer_required": context.transfer_required,
        },
    )
    yield _sse("citations", {"citations": context.citations})

    if context.cached_answer is not None:
        answer = context.cached_answer
        for index in range(0, len(answer), 48):
            yield _sse("delta", {"content": answer[index : index + 48]})
        message_id = await _persist_answer(context, user.id, answer, None)
        yield _sse(
            "done",
            {
                "message_id": message_id,
                "citations": context.citations,
                "disclaimer": DISCLAIMER,
                "transfer_required": False,
                "cached": True,
                "cache_created_at": context.detail.get("cache", {}).get("created_at", ""),
            },
        )
        return

    if not context.documents:
        message_id = await _persist_answer(context, user.id, REFUSAL, None)
        yield _sse("delta", {"content": REFUSAL})
        yield _sse(
            "done",
            {
                "message_id": message_id,
                "citations": context.citations,
                "disclaimer": DISCLAIMER,
                "transfer_required": True,
            },
        )
        return

    config = context.model_config
    model_name = str(config.get("ollama_model", "") if config.get("provider") == "ollama" else "")
    no_think = model_name.lower().startswith("qwen3")
    chain = build_chain(build_chat_model(config), no_think=no_think)
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
    transfer_required = context.transfer_required
    if not answer:
        answer = REFUSAL
        transfer_required = True
    message_id = await _persist_answer(context, user.id, answer, failure)
    if (
        context.cache_eligible
        and failure is None
        and not transfer_required
        and answer != REFUSAL
    ):
        cache_detail = dict(context.detail)
        cache_detail["transfer_required"] = False
        await store_cached_answer(
            context.kb.id,
            context.kb.index_epoch,
            context.strategy.id if context.strategy else None,
            context.strategy_name,
            context.scope,
            context.question,
            answer,
            context.citations,
            cache_detail,
        )
    yield _sse(
        "done",
        {
            "message_id": message_id,
            "citations": context.citations,
            "disclaimer": DISCLAIMER,
            "transfer_required": transfer_required,
            "cached": False,
        },
    )
