"""日志相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RetrievalLogOut(BaseModel):
    """检索日志输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int | None = None
    query: str
    strategy: str | None = None
    top_k: int | None = None
    latency: float | None = None
    result_count: int | None = None
    created_at: datetime


class LogsPage(BaseModel):
    """分页响应（前端兼容 items/total）。"""

    items: list[RetrievalLogOut]
    total: int
    page: int
    page_size: int
