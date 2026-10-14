"""知识库相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.config import settings

#: 构建块开关的合法键
STRATEGY_KEYS = ("vector", "bm25", "fusion", "rerank")


def _check_chunk_strategy(value: str | None) -> str | None:
    if value is None:
        return value
    v = str(value).strip().lower()
    if v not in ("fixed", "recursive"):
        raise ValueError("chunk_strategy 只能是 fixed 或 recursive")
    return v


class KBCreate(BaseModel):
    """新建知识库。enabled_strategies 缺省时全开。"""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    chunk_strategy: str = Field(default_factory=lambda: settings.chunk.default_strategy)
    enabled_strategies: dict[str, bool] | None = None

    @field_validator("chunk_strategy")
    @classmethod
    def _v_strategy(cls, v: str) -> str:
        return _check_chunk_strategy(v) or settings.chunk.default_strategy


class KBUpdate(BaseModel):
    """更新知识库（全部字段可选，只更新传入项）。"""

    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    chunk_strategy: str | None = None
    enabled_strategies: dict[str, bool] | None = None

    @field_validator("chunk_strategy")
    @classmethod
    def _v_strategy(cls, v: str | None) -> str | None:
        return _check_chunk_strategy(v)


class StrategyPayload(BaseModel):
    """单独的构建块开关更新体（PATCH /kb/{id}/strategies）。"""

    vector: bool = True
    bm25: bool = True
    fusion: bool = True
    rerank: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {k: bool(getattr(self, k)) for k in STRATEGY_KEYS}


class KBOut(BaseModel):
    """知识库输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    chunk_strategy: str
    enabled_strategies: dict[str, bool]
    created_at: datetime
    updated_at: datetime
