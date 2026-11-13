import asyncio
import time
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.agent.prompts import AGENT_SYSTEM_PROMPT, FORCE_ANSWER_PROMPT, NO_EVIDENCE_PROMPT
from src.agent.state import AgentState
from src.agent.tools import TOOL_SCHEMAS, chunk_identity, retrieve_documents
from src.db.models import StrategyPreset
from src.generation.chain import REFUSAL, _citation_label

MAX_EVIDENCE = 12


def _format_evidence(state: AgentState, documents: list) -> str:
    if not documents:
        return "未检索到新的可核验依据。可以换用更具体的关键词，或改用其他检索策略。"
    lines = ["检索到以下证据，编号可在回答中直接引用："]
    for document in documents:
        metadata = document.metadata
        index = state.evidence.index(document) + 1
        label = _citation_label(index, metadata)
        snippet = document.page_content.strip()[:500]
        lines.append(f"{label}\n{snippet}")
    return "\n\n".join(lines)


def _fallback_answer(state: AgentState) -> str:
    if not state.evidence:
        return REFUSAL
    lines = ["根据已检索到的制度原文，整理如下，具体以正式文件为准：", ""]
    for index, document in enumerate(state.evidence[:5], start=1):
        snippet = document.page_content.strip()[:200]
        lines.append(f"[{index}] {snippet}")
    return "\n".join(lines)


async def _stream_text(text: str) -> AsyncIterator[dict]:
    for index in range(0, len(text), 48):
        yield {"type": "delta", "content": text[index : index + 48]}


async def run_agent(
    state: AgentState,
    model,
    preset: StrategyPreset | None,
    *,
    history: list | None = None,
    max_rounds: int = 3,
    max_retrievals: int = 3,
    timeout_seconds: int = 90,
) -> AsyncIterator[dict]:
    """执行深度问答决策循环，逐条产出轨迹与回答片段。"""
    bound = model.bind_tools(TOOL_SCHEMAS) if hasattr(model, "bind_tools") else model
    messages = [SystemMessage(content=AGENT_SYSTEM_PROMPT)]
    if history:
        messages.extend(history)
    messages.append(HumanMessage(content=state.question))
    state.messages = messages

    for round_no in range(1, max_rounds + 1):
        state.rounds = round_no
        forced = round_no >= max_rounds or state.retrievals >= max_retrievals
        if forced:
            state.messages.append(SystemMessage(content=FORCE_ANSWER_PROMPT))
        try:
            response = await asyncio.wait_for(
                bound.ainvoke(state.messages), timeout=max(timeout_seconds, 5)
            )
        except asyncio.TimeoutError:
            state.failed = "深度问答超时"
            break
        except Exception as exc:
            state.failed = str(exc)
            break
        state.messages.append(response)
        tool_calls = list(getattr(response, "tool_calls", None) or [])

        if not tool_calls or forced:
            content = str(getattr(response, "content", "") or "").strip()
            if not state.evidence and state.retrievals < max_retrievals and not forced:
                state.messages.append(SystemMessage(content=NO_EVIDENCE_PROMPT))
                continue
            state.final_answer = content or (REFUSAL if not state.evidence else _fallback_answer(state))
            step = {
                "round": round_no,
                "action": "answer",
                "evidence_count": len(state.evidence),
                "note": "强制作答" if forced else "模型直接作答",
            }
            state.trace.append(step)
            yield {"type": "step", "step": step}
            async for event in _stream_text(state.final_answer):
                yield event
            return

        for call in tool_calls:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = str(call.get("id") or name)
            if name == "retrieve":
                if state.retrievals >= max_retrievals:
                    state.messages.append(
                        ToolMessage(content="检索次数已达上限，请基于已有证据作答。", tool_call_id=call_id)
                    )
                    continue
                query = str(args.get("query") or state.question)
                strategy = str(args.get("strategy") or "hybrid_rerank")
                started = time.perf_counter()
                error = ""
                try:
                    documents = await retrieve_documents(
                        state.kb_id,
                        preset,
                        state.scope,
                        query,
                        strategy,
                        args.get("top_k"),
                    )
                except Exception as exc:
                    documents = []
                    error = str(exc)[:200]
                duration_ms = int((time.perf_counter() - started) * 1000)
                state.retrievals += 1
                fresh = []
                for document in documents:
                    identity = chunk_identity(document)
                    if identity in state.seen_chunks:
                        continue
                    if len(state.evidence) >= MAX_EVIDENCE:
                        break
                    state.seen_chunks.add(identity)
                    state.evidence.append(document)
                    fresh.append(document)
                state.messages.append(
                    ToolMessage(
                        content=_format_evidence(state, fresh) if not error else f"检索失败：{error}",
                        tool_call_id=call_id,
                    )
                )
                step = {
                    "round": round_no,
                    "action": "retrieve",
                    "query": query[:120],
                    "strategy": strategy,
                    "hits": len(documents),
                    "new_evidence": len(fresh),
                    "duration_ms": duration_ms,
                    "note": error,
                }
                state.trace.append(step)
                yield {"type": "step", "step": step}
            elif name == "ask_clarification":
                question = str(args.get("question") or "").strip() or "请补充问题涉及的具体事项或时间范围。"
                state.clarifications += 1
                state.final_answer = question
                step = {
                    "round": round_no,
                    "action": "clarify",
                    "note": question[:120],
                }
                state.trace.append(step)
                yield {"type": "step", "step": step}
                async for event in _stream_text(question):
                    yield event
                return
            else:
                state.messages.append(
                    ToolMessage(content=f"不支持的工具：{name}", tool_call_id=call_id)
                )

    if not state.final_answer:
        if state.evidence:
            try:
                final_messages = state.messages + [SystemMessage(content=FORCE_ANSWER_PROMPT)]
                response = await asyncio.wait_for(
                    model.ainvoke(final_messages), timeout=max(timeout_seconds, 5)
                )
                state.final_answer = str(getattr(response, "content", "") or "").strip()
            except Exception as exc:
                state.failed = state.failed or str(exc)
            if not state.final_answer:
                state.final_answer = _fallback_answer(state)
        else:
            state.final_answer = REFUSAL
        step = {
            "round": state.rounds,
            "action": "answer",
            "evidence_count": len(state.evidence),
            "note": "循环结束后的兜底作答" if state.evidence else "无可用依据",
        }
        state.trace.append(step)
        yield {"type": "step", "step": step}
        async for event in _stream_text(state.final_answer):
            yield event
