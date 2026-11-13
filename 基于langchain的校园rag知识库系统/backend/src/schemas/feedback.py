from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

FEEDBACK_TYPES = (
    "答案错误",
    "引用错误或缺失",
    "回答不完整",
    "知识库无相关内容",
    "制度过期",
    "其他",
)


class FeedbackCreateRequest(BaseModel):
    assistant_message_id: int | None = None
    conversation_id: int | None = None
    feedback_type: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=10, max_length=1000)
    expected_answer: str | None = Field(default=None, max_length=1000)
    contact: str | None = Field(default=None, max_length=100)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_no: str
    feedback_type: str
    description: str
    expected_answer: str | None = None
    contact: str | None = None
    status: str
    reply: str | None = None
    reject_reason: str | None = None
    question_snapshot: str
    answer_snapshot: str
    citations_snapshot: list | None = None
    kb_id: int | None = None
    strategy: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class MyFeedbackListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_no: str
    feedback_type: str
    description: str
    status: str
    reply: str | None = None
    created_at: datetime


class FeedbackListResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    page_size: int


class FeedbackUpdateRequest(BaseModel):
    action: str = Field(pattern="^(accept|reply_resolve|resolve|reject|note|reopen)$")
    reply: str | None = Field(default=None, max_length=2000)
    reject_reason: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)


class FeedbackStatsResponse(BaseModel):
    pending: int
    processing: int
    today_new: int
    today_resolved: int
    total: int
