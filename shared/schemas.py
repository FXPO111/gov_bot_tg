# shared/schemas.py
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class IngestRequest(BaseModel):
    url: str = Field(..., description="URL to ingest (html/pdf).")
    title: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class IngestTaskResponse(BaseModel):
    task_id: str


class IngestResponse(BaseModel):
    source_id: UUID
    document_id: UUID
    chunks_upserted: int
    changed: bool


class Citation(BaseModel):
    n: int
    document_id: UUID
    chunk_id: UUID
    title: Optional[str] = None
    url: Optional[str] = None

    path: Optional[str] = None
    heading: Optional[str] = None
    unit_type: Optional[str] = None
    unit_id: Optional[str] = None

    quote: str
    score: float


class ChatRequest(BaseModel):
    question: str
    user_external_id: Optional[int] = None
    chat_id: Optional[UUID] = None

    max_citations: int = 6
    temperature: float = 0.2
    mode: str = "consult"  # brief|consult


class ChatResponse(BaseModel):
    chat_id: UUID
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class TaskStatusResponse(BaseModel):
    task_id: str
    ready: bool
    successful: Optional[bool] = None
    result: Optional[Any] = None
    error: Optional[str] = None
