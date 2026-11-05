"""文档相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    """文档输出（与前端知识库页字段一致）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    filename: str
    file_type: str
    file_size: int | None = None
    status: str
    chunk_count: int = 0
    error_msg: str | None = None
    created_at: datetime
