"""对话相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    """问答请求。strategy 可选：不传时取知识库的 enabled_strategies。

    ``image`` 为图片 data URL（``data:image/png;base64,...``）；传入后走多模态模型。
    question 与 image 至少提供一个（只传图片时按默认问题作答）。
    """

    kb_id: int
    conversation_id: int | None = None
    question: str = Field(default="", max_length=4000)
    strategy: dict[str, bool] | None = None
    image: str | None = Field(default=None, max_length=12_000_000, description="图片 data URL")

    @model_validator(mode="after")
    def _check(self) -> "ChatRequest":
        question = (self.question or "").strip()
        if not question and not self.image:
            raise ValueError("question 与 image 至少提供一个")
        if self.image and not self.image.startswith("data:image/"):
            raise ValueError("image 需为 data URL 格式，如 data:image/png;base64,...")
        if self.image and "," not in self.image:
            raise ValueError("image 缺少 base64 数据段")
        self.question = question
        return self


class ConversationOut(BaseModel):
    """会话输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    title: str | None = None
    created_at: datetime


class MessageOut(BaseModel):
    """消息输出。strategy / stats 由 latency 字段派生（无独立列）。"""

    id: int
    conversation_id: int | None = None
    role: str
    content: str
    citations: list[dict[str, Any]] | None = None
    retrieved_docs: list[dict[str, Any]] | None = None
    latency: dict[str, Any] | None = None
    strategy: str | None = None
    stats: dict[str, Any] | None = None
    created_at: datetime | None = None


class ChatResponse(BaseModel):
    """非流式问答响应（与前端 chat.js 期望一致）。"""

    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_docs: list[dict[str, Any]] = Field(default_factory=list)
    latency: dict[str, Any] = Field(default_factory=dict)
    strategy: str = ""
    stats: dict[str, Any] | None = None
    conversation_id: int
