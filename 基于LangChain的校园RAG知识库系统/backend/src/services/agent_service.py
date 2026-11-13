import logging
import time
from collections.abc import AsyncIterator

from src.agent.loop import run_agent
from src.agent.state import AgentState
from src.generation.chain import (
    DISCLAIMER,
    REFUSAL,
    build_chain,
    build_citations,
    format_context,
)
from src.generation.llm import build_chat_model
from src.retrieval.retrievers import build_retriever
from src.services.chat_service import ChatContext, _persist_answer, _sse

logger = logging.getLogger(__name__)


def _document_details(documents: list) -> list[dict]:
    return [
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
    ]


async def _fallback_fast(context: ChatContext) -> AsyncIterator[dict]:
    """深度问答不可用或失败时退回固定管道，保持引用口径一致。"""
    yield {"type": "step", "step": {"round": 0, "action": "fallback", "note": "深度问答失败，回退快速问答"}}
    try:
        bundle = build_retriever(context.kb.id, context.strategy, context.scope)
        documents = await bundle.retriever.ainvoke(context.question)
    except Exception as exc:
        logger.warning("深度问答回退检索失败：%s", exc)
        context.documents = []
        context.citations = []
        yield {"type": "delta", "content": REFUSAL}
        return
    context.documents = documents
    context.citations = build_citations(documents)
    if not documents:
        yield {"type": "delta", "content": REFUSAL}
        return
    yield {"type": "citations", "citations": context.citations}
    config = context.model_config
    model_name = str(config.get("ollama_model", "") if config.get("provider") == "ollama" else "")
    no_think = model_name.lower().startswith("qwen3")
    chain = build_chain(build_chat_model(config), no_think=no_think)
    parts: list[str] = []
    try:
        async for chunk in chain.astream(
            {
                "context": format_context(documents),
                "question": context.question,
                "history": context.history,
            }
        ):
            text = str(chunk)
            if text:
                parts.append(text)
                yield {"type": "delta", "content": text}
    except Exception as exc:
        logger.warning("深度问答回退生成失败：%s", exc)
    answer = "".join(parts).strip()
    if not answer:
        yield {"type": "delta", "content": REFUSAL}


async def stream_deep_answer(context: ChatContext, user) -> AsyncIterator[str]:
    started = time.perf_counter()
    settings = context.agent_config or {}
    yield _sse(
        "meta",
        {
            "conversation_id": context.conversation_id,
            "user_message_id": context.user_message_id,
            "strategy": context.strategy_name,
            "transfer_required": False,
            "mode": "deep",
        },
    )
    yield _sse("citations", {"citations": []})

    state = AgentState(question=context.question, kb_id=context.kb.id, scope=context.scope)
    failed: str | None = None
    model = build_chat_model(context.model_config, streaming=False)
    try:
        async for event in run_agent(
            state,
            model,
            context.strategy,
            history=context.history,
            max_rounds=int(settings.get("max_rounds", 3)),
            max_retrievals=int(settings.get("max_retrievals", 3)),
            timeout_seconds=int(settings.get("timeout_seconds", 90)),
        ):
            if event.get("type") == "step":
                step = event.get("step") or {}
                if step.get("action") == "retrieve" and state.evidence:
                    yield _sse("citations", {"citations": build_citations(state.evidence)})
                yield _sse("agent_step", {"step": step})
            elif event.get("type") == "delta":
                yield _sse("delta", {"content": event.get("content", "")})
            elif event.get("type") == "citations":
                yield _sse("citations", {"citations": event.get("citations", [])})
    except Exception as exc:
        failed = str(exc)
        logger.warning("深度问答执行失败：%s", exc)

    if not state.final_answer:
        parts: list[str] = []
        async for event in _fallback_fast(context):
            if event.get("type") == "step":
                state.trace.append(event.get("step") or {})
                yield _sse("agent_step", {"step": event.get("step") or {}})
            elif event.get("type") == "citations":
                yield _sse("citations", {"citations": event.get("citations", [])})
            elif event.get("type") == "delta":
                content = event.get("content", "")
                parts.append(content)
                yield _sse("delta", {"content": content})
        state.final_answer = "".join(parts).strip() or REFUSAL

    if context.citations:
        context.citations = build_citations(context.documents) if context.documents else context.citations
    else:
        context.citations = build_citations(state.evidence)

    answer = state.final_answer
    if not answer:
        answer = REFUSAL
    clarification = state.clarifications > 0 and not context.citations
    transfer_required = not context.citations and not clarification
    total_ms = int((time.perf_counter() - started) * 1000)
    retrieval_steps = [item for item in state.trace if item.get("action") == "retrieve"]
    context.detail.update(
        {
            "mode": "deep",
            "agent_rounds": state.rounds,
            "agent_retrievals": state.retrievals,
            "agent_trace": state.trace,
            "agent_failed": failed,
            "agent_clarification": clarification,
            "vector_hits": sum(int(item.get("hits", 0)) for item in retrieval_steps),
            "fused_hits": sum(int(item.get("hits", 0)) for item in retrieval_steps),
            "reranked_hits": len(context.documents or state.evidence),
            "duration_ms": total_ms,
            "versions": sorted(
                {
                    f"{item.metadata.get('document_id')}:{item.metadata.get('version_label')}"
                    for item in (context.documents or state.evidence)
                    if item.metadata.get("document_id")
                }
            ),
            "documents": _document_details(context.documents or state.evidence),
        }
    )
    message_id = await _persist_answer(context, user.id, answer, failed)
    yield _sse(
        "done",
        {
            "message_id": message_id,
            "citations": context.citations,
            "disclaimer": DISCLAIMER,
            "transfer_required": transfer_required,
            "cached": False,
            "mode": "deep",
            "agent_trace": state.trace,
            "agent_rounds": state.rounds,
            "clarification": clarification,
        },
    )
