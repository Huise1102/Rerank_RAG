"""Agent 智能问答相关 Pydantic 模型（第三版）。

与 ``chat.py`` 的差异：请求**去掉 strategy**（策略由 Agent 逐轮决定），响应**新增
agent_trace / rounds / is_clarification**。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class AgentChatRequest(BaseModel):
    """Agent 问答请求。question 与 image 至少提供一个。

    ``image`` 为图片 data URL（``data:image/png;base64,...``）；传入后 Agent 改用多模态模型，
    图片作为决策上下文的一部分参与检索与作答。
    """

    kb_id: int
    conversation_id: int | None = None
    question: str = Field(default="", max_length=4000)
    image: str | None = Field(default=None, max_length=12_000_000, description="图片 data URL")

    @model_validator(mode="after")
    def _check(self) -> "AgentChatRequest":
        question = (self.question or "").strip()
        if not question and not self.image:
            raise ValueError("question 与 image 至少提供一个")
        if self.image and not self.image.startswith("data:image/"):
            raise ValueError("image 需为 data URL 格式，如 data:image/png;base64,...")
        if self.image and "," not in self.image:
            raise ValueError("image 缺少 base64 数据段")
        self.question = question
        return self


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