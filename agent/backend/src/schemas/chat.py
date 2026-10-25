"""会话 / 消息输出模型（问答请求见 schemas/agent.py）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConversationOut(BaseModel):
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
