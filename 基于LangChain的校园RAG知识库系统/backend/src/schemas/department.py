from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    parent_id: int | None = None
    contact: str | None = None
    status: str
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class DepartmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=50, pattern="^[A-Za-z0-9_-]+$")
    parent_id: int | None = None
    contact: str | None = Field(default=None, max_length=200)
    status: str = Field(default="active", pattern="^(active|disabled)$")
    sort_order: int = Field(default=0, ge=0, le=9999)


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: int | None = None
    contact: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    sort_order: int | None = Field(default=None, ge=0, le=9999)
