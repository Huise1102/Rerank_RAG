from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    index: int
    document_id: int | None = None
    filename: str
    page: int | None = None
    snippet: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    kb_id: int
    conversation_id: int | None = None
    strategy_id: int | None = None
    app_key: str = Field(default="user", pattern="^(admin|user)$")


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    kb_id: int | None = None
    app_key: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    kb_id: int | None = None
    strategy_id: int | None = None
    citations: list | None = None
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class RetrievalDetailOut(BaseModel):
    message_id: int
    detail: dict | None = None
