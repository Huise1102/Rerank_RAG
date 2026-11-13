from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

VISIBILITY_PATTERN = "^(public|department|admin_only)$"


class DocumentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    version_label: str
    file_type: str
    file_size: int | None = None
    process_status: str
    lifecycle_status: str
    effective_date: date | None = None
    repealed_at: datetime | None = None
    superseded_by_version_id: int | None = None
    chunk_count: int
    table_count: int
    clause_count: int
    error_msg: str | None = None
    published_at: datetime | None = None
    ingest_report: dict | None = None
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    title: str
    doc_no: str | None = None
    department_id: int | None = None
    department_name: str | None = None
    visibility: str
    owner_user_id: int | None = None
    owner_name: str | None = None
    current_version_id: int | None = None
    review_due_date: date | None = None
    doc_status: str
    filename: str
    file_type: str
    size_bytes: int
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    version: DocumentVersionOut | None = None


class DocumentDetailOut(DocumentOut):
    versions: list[DocumentVersionOut] = []


class DocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    doc_no: str | None = Field(default=None, max_length=100)
    department_id: int | None = None
    visibility: str | None = Field(default=None, pattern=VISIBILITY_PATTERN)
    owner_user_id: int | None = None
    review_due_date: date | None = None


class VersionActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
