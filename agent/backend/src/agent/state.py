"""Agent 状态（state.py）：跨轮保存历史、累积证据与决策轨迹。

证据按 ``chunk_id`` 全链路去重；渲染编号用现有 ``contexts_from_results`` 保证与
``citation.extract_citations`` 的编号回映射一致（跨轮连续）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent.prompts import AGENT_SYSTEM
from src.core.config import settings
from src.generation.prompt import PromptContext, contexts_from_results
from src.retrieval.base import RetrievalResult


@dataclass
class AgentState:
    """一次 Agent 问答的完整中间状态。"""

    question: str
    kb_id: int
    history: list[tuple[str, str]] = field(default_factory=list)
    #: 图片 data URL（可选）；非空时最后一条 user 消息改为内容块数组
    image: str | None = None

    #: 喂给 LLM 的 OpenAI 消息列表（含系统提示、历史、assistant(tool_calls)、tool 结果）
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: 累积证据（按 chunk_id 去重）
    evidences: list[RetrievalResult] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    trace: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0

    def build_initial(self) -> None:
        """构造首轮决策上下文：system + 最近历史 + 当前问题（可带图片）。"""
        system_text = AGENT_SYSTEM
        if self.image:
            system_text = f"{system_text}\n{settings.generation.image_system_rule}"
        self.messages = [{"role": "system", "content": system_text}]
        turns = max(settings.generation.history_max_turns, 0) * 2
        for role, content in list(self.history)[-turns:] if turns else []:
            if role in ("user", "assistant") and content:
                self.messages.append({"role": role, "content": content})

        if not self.image:
            self.messages.append({"role": "user", "content": self.question})
            return

        text = self.question or settings.generation.image_default_question
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{text}\n\n{settings.generation.image_user_notice}"},
                    {"type": "image_url", "image_url": {"url": self.image}},
                ],
            }
        )

    def add_evidences(self, results: list[RetrievalResult]) -> list[RetrievalResult]:
        """追加检索结果（按 chunk_id 去重），返回本轮新增的子序列。"""
        new: list[RetrievalResult] = []
        for r in results:
            cid = str(getattr(r, "chunk_id", ""))
            if cid and cid in self._seen:
                continue
            if cid:
                self._seen.add(cid)
            self.evidences.append(r)
            new.append(r)
        return new

    def evidence_contexts(self) -> list[PromptContext]:
        """全部累积证据的编号上下文（编号从 1 开始，供引用解析）。"""
        return contexts_from_results(self.evidences, start_index=1)

    def append_turn(
        self, assistant_msg: dict[str, Any], tool_result_msg: dict[str, Any]
    ) -> None:
        """把「assistant 的 tool_calls 消息」与「tool 结果消息」追加进决策上下文。"""
        self.messages.append(assistant_msg)
        self.messages.append(tool_result_msg)