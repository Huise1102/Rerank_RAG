"""对话业务逻辑（chat_service.py）。

多轮对话 + 流式输出。生成侧复用 src/generation 模块（prompt / llm / citation），检索侧
复用 src/services/retrieval_service。

关键约束：
- 检索与 LLM 调用都是**阻塞**操作，必须在 `run_in_threadpool` 里执行，避免堵住事件循环。
- 流式先累积完整文本，再由 generation.llm 的 stream_response 统一收尾做引用解析，
  避免分片把 ``[1]`` 切断导致映射失败。
- 消息持久化只存 4 个 JSON 列（citations / retrieved_docs / latency），
  ``strategy`` 与 ``stats`` 无独立列，随 ``latency`` 一起存、读取时再拆出。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Sequence

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.conversation import Conversation
from src.db.models.knowledge_base import KnowledgeBase
from src.db.models.message import Message
from src.db.models.retrieval_log import RetrievalLog
from src.generation.llm import LLMResponse, get_llm_client
from src.generation.prompt import PromptContext, contexts_from_results
from src.services.kb_service import get_enabled_strategies
from src.services.retrieval_service import retrieve
from src.retrieval.pipeline import RetrievalResponse
from src.retrieval.base import RetrievalResult

logger = get_logger(__name__)

#: 异步取阻塞迭代器下一个元素时的"结束"哨兵
_SENTINEL = object()


# --------------------------------------------------------------------------- #
# 会话管理
# --------------------------------------------------------------------------- #


async def create_conversation(
    db: AsyncSession, kb_id: int, *, title: str | None = None
) -> Conversation:
    """新建会话。"""
    conv = Conversation(kb_id=kb_id, title=(title or None))
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversation(db: AsyncSession, conversation_id: int) -> Conversation | None:
    return await db.get(Conversation, conversation_id)


async def list_conversations(db: AsyncSession, kb_id: int | None = None) -> list[Conversation]:
    stmt = select(Conversation)
    if kb_id is not None:
        stmt = stmt.where(Conversation.kb_id == kb_id)
    rows = await db.execute(stmt.order_by(Conversation.id.desc()))
    return list(rows.scalars().all())


async def delete_conversation(db: AsyncSession, conversation_id: int) -> bool:
    conv = await db.get(Conversation, conversation_id)
    if conv is None:
        return False
    await db.delete(conv)
    await db.commit()
    return True


async def list_messages(db: AsyncSession, conversation_id: int) -> list[dict[str, Any]]:
    """读取会话消息，并把 latency 里嵌着的 strategy / stats 拆出来。"""
    rows = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
    )
    out: list[dict[str, Any]] = []
    for m in rows.scalars().all():
        lat = m.latency or {}
        out.append(
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "retrieved_docs": m.retrieved_docs,
                "latency": lat,
                "strategy": lat.get("strategy"),
                "stats": lat.get("stats"),
                "created_at": m.created_at,
            }
        )
    return out


async def _recent_history(db: AsyncSession, conversation_id: int) -> list[tuple[str, str]]:
    """取会话历史为 ``[(role, content), ...]``（供 prompt 组装多轮上下文）。"""
    rows = await db.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
    )
    return [(role, content) for role, content in rows.all()]


# --------------------------------------------------------------------------- #
# 序列化工具
# --------------------------------------------------------------------------- #


def _doc_to_client(r: RetrievalResult) -> dict[str, Any]:
    """把检索结果转成前端 RetrievalDetail 需要的扁平字段。"""
    return {
        "chunk_id": r.chunk_id,
        "rank": r.rank,
        "text": r.text,
        "score": r.final_score,
        "fused_score": r.fused_score,
        "rerank_score": r.rerank_score,
        "matched_by": list(r.matched_by),
        "source": r.source,
        "page": r.page,
    }


def _timing_to_latency(retrieval: RetrievalResponse, generate_seconds: float) -> dict[str, Any]:
    """把检索耗时（秒）与生成耗时合并成前端 latency（毫秒）结构。"""
    t = retrieval.timing or {}
    recall_ms = round(float(t.get("recall", 0.0)) * 1000)
    retrieve_ms = round(float(t.get("total", 0.0)) * 1000)
    rerank_ms = round(float(t.get("rerank", 0.0)) * 1000)
    generate_ms = round(generate_seconds * 1000)
    return {
        "recall_ms": recall_ms,
        "retrieve_ms": retrieve_ms,
        "rerank_ms": rerank_ms,
        "generate_ms": generate_ms,
        "total_ms": retrieve_ms + generate_ms,
        "strategy": retrieval.strategy,
        "stats": retrieval.stats,
    }


def _citations_payload(response: LLMResponse) -> list[dict[str, Any]]:
    return response.citations.to_dict()["citations"]


def _resolve_strategies(
    kb: KnowledgeBase, override: dict[str, bool] | None
) -> dict[str, bool]:
    if override:
        from src.services.kb_service import normalize_strategies

        return normalize_strategies(override)
    return get_enabled_strategies(kb)


# --------------------------------------------------------------------------- #
# 检索执行 / 落库
# --------------------------------------------------------------------------- #


async def _run_retrieval(
    kb: KnowledgeBase, question: str, strategies: dict[str, bool]
) -> RetrievalResponse:
    """在线程池执行阻塞检索。"""
    return await run_in_threadpool(retrieve, kb.id, question, strategies)


async def _log_retrieval(db: AsyncSession, retrieval: RetrievalResponse, kb_id: int) -> None:
    """写一条检索日志（失败不阻断主流程）。"""
    try:
        log = RetrievalLog(
            kb_id=kb_id,
            query=retrieval.query,
            strategy=retrieval.strategy,
            top_k=(retrieval.params or {}).get("top_k"),
            latency=round(float((retrieval.timing or {}).get("total", 0.0)), 4),
            result_count=len(retrieval.results),
        )
        db.add(log)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning("写检索日志失败: %s", exc)


async def _save_message(
    db: AsyncSession,
    conversation_id: int,
    *,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    retrieved_docs: list[dict[str, Any]] | None = None,
    latency: dict[str, Any] | None = None,
    touch_title: str | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        citations=citations,
        retrieved_docs=retrieved_docs,
        latency=latency,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # 首条用户消息作为会话标题
    if touch_title:
        conv = await db.get(Conversation, conversation_id)
        if conv is not None and not conv.title:
            conv.title = touch_title[:60]
            await db.commit()
    return msg


# --------------------------------------------------------------------------- #
# 非流式问答
# --------------------------------------------------------------------------- #


async def chat(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    *,
    conversation_id: int | None = None,
    strategies: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """执行一次完整问答（非流式），返回前端 ChatResponse 结构。"""
    sw = _resolve_strategies(kb, strategies)

    if conversation_id is None:
        conv = await create_conversation(db, kb.id, title=question[:60])
        conversation_id = conv.id

    history = await _recent_history(db, conversation_id)
    await _save_message(db, conversation_id, role="user", content=question, touch_title=question)

    retrieval = await _run_retrieval(kb, question, sw)
    contexts = contexts_from_results(retrieval.results)

    llm = get_llm_client()
    response: LLMResponse = await run_in_threadpool(
        llm.generate, question, contexts, history=history
    )
    generate_seconds = response.elapsed

    latency = _timing_to_latency(retrieval, generate_seconds)
    citations = _citations_payload(response)
    docs = [_doc_to_client(r) for r in retrieval.results]

    await _save_message(
        db, conversation_id, role="assistant", content=response.answer,
        citations=citations, retrieved_docs=docs, latency=latency,
    )
    await _log_retrieval(db, retrieval, kb.id)

    logger.info(
        "问答完成 kb=%d conv=%d 策略=%s 引用=%d 耗时=%.2fs",
        kb.id, conversation_id, retrieval.strategy, len(citations), latency["total_ms"] / 1000,
    )

    return {
        "answer": response.answer,
        "citations": citations,
        "retrieved_docs": docs,
        "latency": latency,
        "strategy": retrieval.strategy,
        "stats": retrieval.stats,
        "conversation_id": conversation_id,
    }


# --------------------------------------------------------------------------- #
# 流式问答
# --------------------------------------------------------------------------- #


def sse(payload: dict[str, Any]) -> str:
    """把事件编码为 SSE 一行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def prepare_stream(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    *,
    conversation_id: int | None = None,
    strategies: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """流式前的准备工作：建会话、存用户消息、检索、拿 LLM 增量迭代器。"""
    sw = _resolve_strategies(kb, strategies)

    if conversation_id is None:
        conv = await create_conversation(db, kb.id, title=question[:60])
        conversation_id = conv.id

    history = await _recent_history(db, conversation_id)
    await _save_message(db, conversation_id, role="user", content=question, touch_title=question)

    retrieval = await _run_retrieval(kb, question, sw)
    contexts = contexts_from_results(retrieval.results)
    docs = [_doc_to_client(r) for r in retrieval.results]

    iterator, box = get_llm_client().stream_response(question, contexts, history=history)

    return {
        "kb_id": kb.id,
        "conversation_id": conversation_id,
        "retrieval": retrieval,
        "iterator": iterator,
        "box": box,
        "docs": docs,
        "start": time.perf_counter(),
    }


async def stream_events(bundle: dict[str, Any]) -> AsyncIterator[str]:
    """按 SSE 协议产出 meta / delta / done 事件，并在结束时落库。"""
    retrieval: RetrievalResponse = bundle["retrieval"]
    conversation_id: int = bundle["conversation_id"]

    yield sse(
        {
            "type": "meta",
            "conversation_id": conversation_id,
            "strategy": retrieval.strategy,
            "stats": retrieval.stats,
            "retrieved_docs": bundle["docs"],
            "latency": _timing_to_latency(retrieval, 0.0),
        }
    )

    iterator = bundle["iterator"]
    try:
        while True:
            piece = await asyncio.to_thread(next, iterator, _SENTINEL)
            if piece is _SENTINEL:
                break
            yield sse({"type": "delta", "delta": piece})
    except Exception as exc:  # noqa: BLE001 - 流式中断也要给出收尾事件
        logger.warning("流式生成中断: %s", exc)
        yield sse({"type": "error", "error": f"{type(exc).__name__}: {exc}"})

    response: LLMResponse | None = bundle["box"].get("response")
    if response is None:
        yield sse({"type": "done", "response": {"answer": "", "conversation_id": conversation_id}})
        return

    generate_seconds = response.elapsed
    latency = _timing_to_latency(retrieval, generate_seconds)
    citations = _citations_payload(response)

    # 用独立会话落库：请求级会话在流式响应结束前可能已被依赖清理
    try:
        from src.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db2:
            await _save_message(
                db2, conversation_id, role="assistant", content=response.answer,
                citations=citations, retrieved_docs=bundle["docs"], latency=latency,
            )
            await _log_retrieval(db2, retrieval, bundle["kb_id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("流式落库失败: %s", exc)

    yield sse(
        {
            "type": "done",
            "response": {
                "answer": response.answer,
                "citations": citations,
                "retrieved_docs": bundle["docs"],
                "latency": latency,
                "strategy": retrieval.strategy,
                "stats": retrieval.stats,
                "conversation_id": conversation_id,
            },
        }
    )
