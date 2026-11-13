from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EvaluationRunRequest(BaseModel):
    kind: str = Field(default="retrieval", pattern="^(retrieval|generation)$")
    kb_id: int
    testset_path: str = Field(min_length=1, max_length=300)
    strategy_id: int | None = None
    limit: int | None = Field(default=20, ge=1, le=400)
    top_k: int = Field(default=10, ge=1, le=50)
    role: str = Field(default="admin", pattern="^(admin|dept_admin|staff|student)$")
    department_id: int | None = None


class EvaluationValidateRequest(BaseModel):
    testset_path: str = Field(min_length=1, max_length=300)


class EvaluationReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    status: str
    testset: str | None = None
    summary: dict | None = None
    markdown: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
