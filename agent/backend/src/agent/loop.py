"""调度循环（loop.py）：第三版核心 —— LLM 逐轮决策，最多 3 轮必须收敛。

ReAct 式循环：每轮 LLM 要么调用一个工具（retrieve / ask_clarification），要么直接输出
最终答案（不调工具即 content）。三条保底对应「最多 3 次决策」硬约束：
1. ``MAX_DECISION_ROUNDS`` 封顶 for 循环；
2. 不调工具直接输出 content = 最终答案，立即退出；
3. 触顶/异常后追加「必须作答」指令强制 LLM 输出，绝不无限循环。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from src.agent.prompts import FORCE_ANSWER_INSTRUCTION, TOOL_SCHEMAS
from src.agent.state import AgentState
from src.agent.tools import _doc_to_client, execute_retrieve
from src.core.config import settings
from src.generation.citation import extract_citations
from src.generation.llm import get_llm_client

#: 硬约束：单问题最多决策轮数（第 3 轮必须收敛）
MAX_DECISION_ROUNDS: int = settings.agent.max_decision_rounds


@dataclass
class AgentResult:
    """一次 Agent 问答的完整结果。"""

    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieved_docs: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    latency: dict[str, Any] = field(default_factory=dict)
    is_clarification: bool = False
    clarification_question: str | None = None


class AgentLoop:
    """LLM 驱动的检索决策循环。用法：``AgentLoop().run(question, kb_id, history)``。"""

    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_llm_client()
        self.max_rounds = MAX_DECISION_ROUNDS
        self.max_retrieve_calls = settings.agent.max_retrieve_calls

    # ---- 公开入口 ----

    def run(self, question: str, kb_id: int, history: list[tuple[str, str]],
            image: str | None = None) -> AgentResult:
        """非流式入口：消费流式事件，只取最终结果。"""
        result: AgentResult | None = None
        for event in self.run_stream(question, kb_id, history, image):
            if event["type"] == "result":
                result = event["result"]
        if result is None:
            raise RuntimeError("Agent 未产出最终结果")
        return result

    def run_stream(self, question: str, kb_id: int, history: list[tuple[str, str]],
                   image: str | None = None) -> Iterator[dict[str, Any]]:
        """流式决策循环。

        yield ``{"type": "step"}``（每轮决策轨迹）、``{"type": "delta"}``（最终答案文本增量）、
        ``{"type": "result"}``（结束，含 AgentResult）。决策轮调工具时通常不产出正文，
        因此 delta 可直接向前端推送，无需等整轮结束。
        """
        state = AgentState(question=question, kb_id=kb_id, history=history, image=image)
        state.build_initial()
        retrieve_calls = 0

        for round_no in range(1, self.max_rounds + 1):
            state.rounds = round_no
            round_t0 = time.perf_counter()
            decision = None
            for kind, payload in self._decide_stream(state):
                if kind == "delta":
                    yield {"type": "delta", "delta": payload}
                else:
                    decision = payload
            if decision is None:
                raise RuntimeError("LLM 未返回决策结果")

            # 不调工具 = 直接输出最终答案（含 [n] 引用）→ 结束
            if not decision.tool_calls:
                step = self._step(round_no, "answer", tool=None, args=None,
                                  usage=decision.usage, elapsed_ms=self._elapsed(round_t0))
                state.trace.append(step)
                yield {"type": "step", "step": step}
                yield {"type": "result", "result": self._finish(state, decision.content)}
                return

            call = decision.tool_calls[0]
            name = call.get("name", "")
            args = self._parse_args(call.get("arguments", ""))

            if name == "ask_clarification":
                step = self._step(round_no, "ask_clarification", tool=name, args=args,
                                  usage=decision.usage, elapsed_ms=self._elapsed(round_t0))
                state.trace.append(step)
                yield {"type": "step", "step": step}
                yield {"type": "delta", "delta": (args.get("question") or "").strip()}
                yield {"type": "result", "result": self._clarify(state, (args.get("question") or "").strip())}
                return

            # 触发上限：未知工具 / 检索次数用尽 → 强制作答
            query = (args.get("query") or "").strip()
            if name != "retrieve" or not query or retrieve_calls >= self.max_retrieve_calls:
                step = self._step(round_no, "force_answer", tool=name or None, args=args,
                                  usage=decision.usage, elapsed_ms=self._elapsed(round_t0))
                state.trace.append(step)
                yield {"type": "step", "step": step}
                for event in self._force_finish_stream(state):
                    yield event
                return

            # 执行检索：回填证据，进入下一轮
            retrieve_calls += 1
            result = execute_retrieve(state, query, args.get("strategy") or "hybrid_rerank")
            state.append_turn(
                self._assistant_tool_calls_msg(call),
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": result["rendered"]},
            )
            step = self._step(round_no, "retrieve", tool="retrieve",
                              args={"query": query, "strategy": result["strategy"]},
                              result_count=result["result_count"], docs=result["docs"],
                              strategy=result["strategy"], stats=result["stats"],
                              usage=decision.usage, elapsed_ms=self._elapsed(round_t0))
            state.trace.append(step)
            yield {"type": "step", "step": step}

        # 触顶仍未收敛：追加「必须作答」指令，强制 LLM 输出
        for event in self._force_finish_stream(state):
            yield event

    # ---- 内部 ----

    def _decide_stream(self, state: AgentState) -> Iterator[tuple[str, Any]]:
        """单轮流式决策：透传正文增量，最后返回聚合后的 ChatMessage。"""
        return self.llm.chat_stream(
            state.messages,
            tools=TOOL_SCHEMAS,
            model=self._model(state),
            temperature=settings.agent.temperature,
            max_tokens=settings.agent.max_tokens,
        )

    @staticmethod
    def _model(state: AgentState) -> str:
        """带图时改用多模态模型（文本模型收到 image_url 块会直接 400）。"""
        return settings.llm.vision_model if state.image else settings.agent.model

    @staticmethod
    def _parse_args(raw: Any) -> dict[str, Any]:
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _assistant_tool_calls_msg(call: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.get("id", ""),
                    "type": "function",
                    "function": {"name": call.get("name", ""), "arguments": call.get("arguments", "")},
                }
            ],
        }

    @staticmethod
    def _elapsed(t0: float) -> int:
        return round((time.perf_counter() - t0) * 1000)

    @staticmethod
    def _step(round_no: int, action: str, *, tool: str | None, args: dict[str, Any] | None,
              result_count: int = 0, docs: list[dict[str, Any]] | None = None,
              strategy: str = "", stats: dict[str, Any] | None = None,
              usage: dict[str, Any] | None = None, elapsed_ms: int = 0) -> dict[str, Any]:
        return {
            "round": round_no,
            "action": action,
            "tool": tool,
            "args": args,
            "result_count": result_count,
            "docs": docs or [],
            "strategy": strategy,
            "stats": stats,
            "llm_usage": usage or {},
            "elapsed_ms": elapsed_ms,
        }

    def _finish(self, state: AgentState, answer: str) -> AgentResult:
        citations = extract_citations(answer, state.evidence_contexts()).to_dict()["citations"]
        return AgentResult(
            answer=answer,
            citations=citations,
            retrieved_docs=[_doc_to_client(r) for r in state.evidences],
            trace=state.trace,
            rounds=state.rounds,
        )

    def _clarify(self, state: AgentState, question: str) -> AgentResult:
        question = question or "请补充更多信息，以便我准确回答。"
        return AgentResult(
            answer=question,
            citations=[],
            retrieved_docs=[],
            trace=state.trace,
            rounds=state.rounds,
            is_clarification=True,
            clarification_question=question,
        )

    def _force_finish_stream(self, state: AgentState) -> Iterator[dict[str, Any]]:
        """触顶/异常后的强制作答：不带 tools，正文边生成边推送。"""
        state.messages.append({"role": "user", "content": FORCE_ANSWER_INSTRUCTION})
        round_t0 = time.perf_counter()
        decision = None
        for kind, payload in self.llm.chat_stream(
            state.messages,
            model=self._model(state),
            temperature=settings.agent.temperature,
            max_tokens=settings.agent.max_tokens,
        ):
            if kind == "delta":
                yield {"type": "delta", "delta": payload}
            else:
                decision = payload
        if decision is None:
            raise RuntimeError("LLM 未返回强制作答结果")
        step = self._step(state.rounds, "force_answer", tool=None, args=None,
                          usage=decision.usage, elapsed_ms=self._elapsed(round_t0))
        state.trace.append(step)
        yield {"type": "step", "step": step}
        yield {"type": "result", "result": self._finish(state, decision.content or "")}