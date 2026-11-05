"""知识库表（无用户体系）。enabled_strategies 存 4 个构建块开关。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

#: 默认全开的构建块开关
DEFAULT_STRATEGIES: dict[str, bool] = {"vector": True, "bm25": True, "fusion": True, "rerank": True}


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_strategy: Mapped[str] = mapped_column(String(50), default="recursive", nullable=False)
    enabled_strategies: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=lambda: dict(DEFAULT_STRATEGIES), nullable=False
    )
    #: 启用开关：仅启用的知识库可在「问答」页选用
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
