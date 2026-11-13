from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KbCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    status: str = Field(default="published", pattern="^(published|unpublished)$")


class KbUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, pattern="^(published|unpublished)$")


class KbOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    filename: str
    file_type: str
    size_bytes: int
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    use_vector: bool
    use_bm25: bool
    vector_weight: float
    bm25_weight: float
    rrf_k: int
    rerank: bool
    vector_top_k: int
    bm25_top_k: int
    rerank_top_n: int
    is_open: bool
    is_default: bool


class StrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=300)
    use_vector: bool = True
    use_bm25: bool = True
    vector_weight: float = Field(default=0.5, ge=0, le=1)
    bm25_weight: float = Field(default=0.5, ge=0, le=1)
    rrf_k: int = Field(default=60, ge=1, le=500)
    rerank: bool = True
    vector_top_k: int = Field(default=50, ge=1, le=100)
    bm25_top_k: int = Field(default=50, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=20)
    is_open: bool = True
    is_default: bool = False


class StrategyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=300)
    use_vector: bool | None = None
    use_bm25: bool | None = None
    vector_weight: float | None = Field(default=None, ge=0, le=1)
    bm25_weight: float | None = Field(default=None, ge=0, le=1)
    rrf_k: int | None = Field(default=None, ge=1, le=500)
    rerank: bool | None = None
    vector_top_k: int | None = Field(default=None, ge=1, le=100)
    bm25_top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    is_open: bool | None = None
    is_default: bool | None = None


class ModelConfigRequest(BaseModel):
    provider: Literal["api", "ollama"]
    api_base_url: str = Field(min_length=1, max_length=300)
    api_model: str = Field(min_length=1, max_length=100)
    ollama_base_url: str = Field(min_length=1, max_length=300)
    ollama_model: str = Field(min_length=1, max_length=100)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=64, le=8192)


class ModelConfigOut(BaseModel):
    provider: Literal["api", "ollama"]
    api_base_url: str
    api_model: str
    ollama_base_url: str
    ollama_model: str
    temperature: float
    max_tokens: int
    api_key_configured: bool
    active_model: str
    active_base_url: str
    updated_at: str = ""
