"""Agent 智能问答门面（agent_service.py）：复用 chat_service 的会话/消息/SSE 基础。

Agent 多轮决策为顺序阻塞，放在 ``run_in_threadpool`` 执行，避免堵住事件循环。
最终答案由 Agent 直接产出（方案 A），SSE 承载「决策步骤时间线 + 最终答案」：
meta → agent_step×N → delta → done。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, AsyncIterator

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.loop import AgentLoop
from src.core.logging import get_logger
from src.db.models.knowledge_base import KnowledgeBase
from src.services.chat_service import (
    _recent_history,
    _save_message,
    create_conversation,
    sse,
)

logger = get_logger(__name__)


def _run_agent(kb_id: int, question: str, history: list[tuple[str, str]],
               image: str | None = None) -> Any:
    """在线程池跑一次 AgentLoop（阻塞）。"""
    return AgentLoop().run(question, kb_id, history, image)


def _latency_payload(result: Any, total_ms: int) -> dict[str, Any]:
    """把 Agent 结果打包进 latency（含 agent_trace，历史回看由此还原时间线）。"""
    return {
        "rounds": result.rounds,
        "total_ms": total_ms,
        "agent_trace": result.trace,
        "is_clarification": result.is_clarification,
    }


def _response_payload(result: Any, latency: dict[str, Any], conversation_id: int) -> dict[str, Any]:
    return {
        "answer": result.answer,
        "citations": result.citations,
        "retrieved_docs": result.retrieved_docs,
        "latency": latency,
        "agent_trace": result.trace,
        "rounds": result.rounds,
        "is_clarification": result.is_clarification,
        "clarification_question": result.clarification_question,
        "conversation_id": conversation_id,
    }


async def agent_chat(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    *,
    conversation_id: int | None = None,
    image: str | None = None,
) -> dict[str, Any]:
    """一次完整 Agent 问答（非流式）。"""
    text = (question or "").strip()
    user_content = text or "[图片]"

    if conversation_id is None:
        conv = await create_conversation(db, kb.id, title=(text or "图片问答")[:60])
        conversation_id = conv.id

    history = await _recent_history(db, conversation_id)
    await _save_message(db, conversation_id, role="user", content=user_content, touch_title=user_content)

    t0 = time.perf_counter()
    result = await run_in_threadpool(_run_agent, kb.id, text, history, image)
    total_ms = round((time.perf_counter() - t0) * 1000)

    latency = _latency_payload(result, total_ms)
    await _save_message(
        db, conversation_id, role="assistant", content=result.answer,
        citations=result.citations, retrieved_docs=result.retrieved_docs, latency=latency,
    )

    logger.info(
        "Agent 问答完成 kb=%d conv=%d 轮数=%d 引用=%d 耗时=%.2fs%s",
        kb.id, conversation_id, result.rounds, len(result.citations), total_ms / 1000,
        "（澄清）" if result.is_clarification else "",
    )
    return _response_payload(result, latency, conversation_id)


async def prepare_agent_stream(
    db: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    *,
    conversation_id: int | None = None,
    image: str | None = None,
) -> dict[str, Any]:
    """流式前准备：建会话、存用户消息；Agent 循环留到事件生成阶段执行。"""
    text = (question or "").strip()
    user_content = text or "[图片]"

    if conversation_id is None:
        conv = await create_conversation(db, kb.id, title=(text or "图片问答")[:60])
        conversation_id = conv.id

    history = await _recent_history(db, conversation_id)
    await _save_message(db, conversation_id, role="user", content=user_content, touch_title=user_content)

    return {
        "kb_id": kb.id,
        "conversation_id": conversation_id,
        "question": text,
        "history": history,
        "image": image,
        "t0": time.perf_counter(),
    }


def _stream_agent_events(bundle: dict[str, Any], out: "queue.Queue[dict[str, Any] | None]") -> None:
    """线程池任务：跑 AgentLoop 并把事件放进队列（None 为结束哨兵）。"""
    try:
        loop = AgentLoop()
        for event in loop.run_stream(bundle["question"], bundle["kb_id"], bundle["history"], bundle.get("image")):
            out.put(event)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent 流式执行失败")
        out.put({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        out.put(None)


async def agent_stream_events(bundle: dict[str, Any]) -> AsyncIterator[str]:
    """按 SSE 协议实时产出：meta → (agent_step | delta)×N → done。

    Agent 循环是同步阻塞的，放独立线程执行；事件经队列转发，正文因此可以边生成边下发。
    结束时用独立会话落库（请求级会话在流式响应结束前可能已被依赖清理）。
    """
    conversation_id: int = bundle["conversation_id"]
    yield sse({"type": "meta", "conversation_id": conversation_id, "agent": True})

    out: queue.Queue[dict[str, Any] | None] = queue.Queue()
    threading.Thread(target=_stream_agent_events, args=(bundle, out), daemon=True).start()

    result = None
    while True:
        event = await run_in_threadpool(out.get)
        if event is None:
            break
        kind = event.get("type")
        if kind == "delta":
            yield sse({"type": "delta", "delta": event["delta"]})
        elif kind == "step":
            yield sse({"type": "agent_step", "step": event["step"]})
        elif kind == "result":
            result = event["result"]
        elif kind == "error":
            yield sse({"type": "error", "error": event["error"]})
            return

    if result is None:
        yield sse({"type": "error", "error": "Agent 未产出结果"})
        return

    total_ms = round((time.perf_counter() - bundle["t0"]) * 1000)
    latency = _latency_payload(result, total_ms)

    try:
        from src.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db2:
            await _save_message(
                db2, conversation_id, role="assistant", content=result.answer,
                citations=result.citations, retrieved_docs=result.retrieved_docs, latency=latency,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent 流式落库失败: %s", exc)

    yield sse({"type": "done", "response": _response_payload(result, latency, conversation_id)})