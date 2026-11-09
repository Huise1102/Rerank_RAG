"""调度循环，由 LLM 逐轮决策，最多 3 轮必须收敛。

每轮 LLM 要么调用一个工具，要么直接输出最终答案。
四项保底机制：轮数上限封顶循环；不调用工具即视为最终答案并退出；
触顶或异常后追加必须作答的指令；进入循环前先判断正常交流还是知识库问答。
正常交流直接作答不检索，知识库问答启用检索优先守卫，带图提问按图片分支放行。
检索优先规则下，首次检索前不直接作答，正文先缓存不推送，
模型仍不调用检索时追加系统拦截指令并要求重新决策，再拒绝由系统兜底检索一次。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from src.agent.prompts import (
    FORCE_ANSWER_INSTRUCTION,
    INTENT_SYSTEM,
    RETRIEVE_FIRST_INSTRUCTION,
    TOOL_SCHEMAS,
)
from src.agent.state import AgentState
from src.agent.tools import _doc_to_client, execute_retrieve
from src.core.config import settings
from src.core.model_config import active_model
from src.generation.agent_llm import get_agent_llm
from src.generation.citation import extract_citations
from src.generation.llm import ChatMessage

# 单问题最多决策轮数，第 3 轮必须收敛
MAX_DECISION_ROUNDS: int = settings.agent.max_decision_rounds

# 意图识别只带最近 N 条历史，避免长上下文拖慢分类
INTENT_HISTORY_MESSAGES: int = 4


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
    """LLM 驱动的检索决策循环。"""

    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_agent_llm()
        self.max_rounds = MAX_DECISION_ROUNDS
        self.max_retrieve_calls = settings.agent.max_retrieve_calls

    # 公开入口

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

        依次产出决策步骤、最终答案文本增量与带结果对象的结束事件。
        决策轮调用工具时通常不产出正文，因此文本增量可直接推送给前端。
        """

        state = AgentState(question=question, kb_id=kb_id, history=history, image=image)
        intent, intent_usage, intent_ms = self._classify_intent(question, history, image)
        state.intent = intent
        state.build_initial()

        intent_step = self._step(0, "intent", tool=None, args={"intent": intent},
                                 usage=intent_usage, elapsed_ms=intent_ms)
        state.trace.append(intent_step)
        yield {"type": "step", "step": intent_step}

        retrieve_calls = 0
        nudged = False

        for round_no in range(1, self.max_rounds + 1):
            state.rounds = round_no
            round_t0 = time.perf_counter()
            decision: ChatMessage | None = None
            buffered: list[str] = []

            if state.evidences:
                # 已有证据：正文边生成边推送（真正的流式回答）
                for kind, payload in self._decide_stream(state):
                    if kind == "delta":
                        yield {"type": "delta", "delta": payload}
                    else:
                        decision = payload
            else:
                # 尚未检索：先缓存正文，确认模型是否真的调用了工具
                decision, buffered = self._decide_buffered(state)

            if decision is None:
                raise RuntimeError("LLM 未返回决策结果")

            # 检索优先：意图为知识库问答、且尚未检索过时，拦截「凭记忆直接作答」
            if not decision.tool_calls and retrieve_calls == 0 and state.intent == "kb":
                if not nudged:
                    nudged = True
                    state.messages.append({"role": "user", "content": RETRIEVE_FIRST_INSTRUCTION})
                    decision, buffered = self._decide_buffered(state)
                    if decision is None:
                        raise RuntimeError("LLM 未返回决策结果")
                if not decision.tool_calls:
                    # 模型仍拒绝检索：由系统用原问题兜底检索，下一轮带着证据作答
                    buffered = []
                    decision = self._forced_retrieve_call(state.question)

            # 不调用工具即直接输出最终答案，流程结束
            if not decision.tool_calls:
                for text in buffered:
                    yield {"type": "delta", "delta": text}
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

            # 触发上限，遇到未知工具或检索次数用尽时强制作答
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
                              args={"query": query, "strategy": result["strategy"],
                                    "top_k": result["top_k"]},
                              result_count=result["result_count"], docs=result["docs"],
                              strategy=result["strategy"], stats=result["stats"],
                              timing=result["timing"],
                              usage=decision.usage, elapsed_ms=self._elapsed(round_t0))
            state.trace.append(step)
            yield {"type": "step", "step": step}

        # 触顶仍未收敛：追加「必须作答」指令，强制 LLM 输出
        for event in self._force_finish_stream(state):
            yield event

    # 内部实现

    def _classify_intent(
        self, question: str, history: list[tuple[str, str]], image: str | None = None
    ) -> tuple[str, dict[str, Any], int]:
        """意图识别，先判断正常交流还是知识库问答，再进入决策循环。

        带图提问直接归为图片意图，图片本身就是参考资料，不为描述图片强行检索；
        分类失败或无法解析时回落到知识库问答，宁可多检索一次也不放过幻觉。
        返回意图、用量与耗时。
        """
        if image:
            return "image", {}, 0

        messages: list[dict[str, Any]] = [{"role": "system", "content": INTENT_SYSTEM}]
        for role, content in list(history)[-INTENT_HISTORY_MESSAGES:]:
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content[:300]})
        messages.append({"role": "user", "content": question})

        t0 = time.perf_counter()
        try:
            resp = self.llm.chat(
                messages,
                model=active_model(),
                temperature=0.0,
                max_tokens=16,
            )
        except Exception:  # noqa: BLE001 —— 分类失败不应阻断问答
            return "kb", {}, self._elapsed(t0)

        label = (resp.content or "").strip().upper()
        intent = "chat" if label.startswith("CHAT") else "kb"
        return intent, resp.usage or {}, self._elapsed(t0)

    def _decide_stream(self, state: AgentState) -> Iterator[tuple[str, Any]]:
        """单轮流式决策：透传正文增量，最后返回聚合后的 ChatMessage。"""
        return self.llm.chat_stream(
            state.messages,
            tools=TOOL_SCHEMAS,
            model=self._model(state),
            temperature=settings.agent.temperature,
            max_tokens=settings.agent.max_tokens,
        )

    def _decide_buffered(self, state: AgentState) -> tuple[ChatMessage, list[str]]:
        """尚未检索时的决策：先缓存正文，避免把「凭记忆作答」推给前端。"""
        pieces: list[str] = []
        decision: ChatMessage | None = None
        for kind, payload in self._decide_stream(state):
            if kind == "delta":
                pieces.append(payload)
            else:
                decision = payload
        if decision is None:
            raise RuntimeError("LLM 未返回决策结果")
        return decision, pieces

    @staticmethod
    def _forced_retrieve_call(question: str) -> ChatMessage:
        """模型拒绝检索时，由系统构造一次兜底检索调用（下一轮带证据作答）。"""
        args = {"query": question, "strategy": "hybrid_rerank", "forced": True}
        return ChatMessage(
            tool_calls=[
                {
                    "id": "call_forced_retrieve",
                    "name": "retrieve",
                    "arguments": json.dumps(args, ensure_ascii=False),
                }
            ]
        )

    @staticmethod
    def _model(state: AgentState) -> str:
        """按当前模型配置选择模型；带图时优先多模态模型。"""
        return active_model(image=bool(state.image))

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
              timing: dict[str, float] | None = None,
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
            "timing": timing or {},
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