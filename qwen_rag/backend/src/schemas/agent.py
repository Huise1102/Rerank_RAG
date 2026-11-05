"""Agent 智能问答相关 Pydantic 模型。

本地 Qwen3-8B 是纯文本模型，因此请求不再接受 ``image`` 字段（agent 版的多模态能力
在 qwen_rag 中下线）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentChatRequest(BaseModel):
    """Agent 问答请求。"""

    kb_id: int
    conversation_id: int | None = None
    question: str = Field(default="", max_length=4000)

    @field_validator("question")
    @classmethod
    def _check(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("question 不能为空")
        return text


class AgentChatResponse(BaseModel):
    """非流式 Agent 问答响应（与前端 agent.js 期望一致）。"""

    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_docs: list[dict[str, Any]] = Field(default_factory=list)
    latency: dict[str, Any] = Field(default_factory=dict)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    rounds: int = 0
    is_clarification: bool = False
    clarification_question: str | None = None
    conversation_id: int
