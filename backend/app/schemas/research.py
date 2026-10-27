"""Pydantic schemas for persisted research records (RAG and Agent runs)."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.rag import (
    AnswerGeneration,
    CitationIntegrity,
    HistoryLink,
    RagEntityReference,
    RagRelationshipReference,
    RagSourceEvidence,
)


__all__ = [
    "HistoryLink",
    "ResearchRecordSummary",
    "ResearchHistoryListResponse",
    "ResearchRecordDetail",
    "build_generation_from_record",
    "build_research_detail",
    "build_research_summary",
]


class ResearchRecordSummary(BaseModel):
    """One row of the research history list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    mode: str
    question: str
    status: str
    evidence_sufficient: bool
    generation_mode: Optional[str] = None
    failure_reason: Optional[str] = None
    citation_valid: Optional[bool] = None
    source_count: int = 0
    elapsed_ms: Optional[float] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    created_at: datetime


class ResearchHistoryListResponse(BaseModel):
    """Paginated research history."""

    items: List[ResearchRecordSummary] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class ResearchRecordDetail(BaseModel):
    """Full saved research record: answer plus the evidence snapshot taken at run time.

    The detail endpoint returns exactly what was stored; it never re-runs
    retrieval or the model, so a reopened answer cannot drift.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    mode: str
    question: str
    answer: str
    status: str
    evidence_sufficient: bool
    generation: AnswerGeneration
    intent: Optional[Dict[str, Any]] = None
    sources: List[RagSourceEvidence] = Field(default_factory=list)
    entities: List[RagEntityReference] = Field(default_factory=list)
    relationships: List[RagRelationshipReference] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    elapsed_ms: Optional[float] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


def build_generation_from_record(record) -> AnswerGeneration:
    """Reconstruct the generation status stored with a record (never re-derived)."""
    citation_check = None
    if record.citation_valid is not None:
        citation_check = CitationIntegrity(
            valid=bool(record.citation_valid),
            cited_ids=[],
            invalid_ids=list(record.citation_invalid_ids or []),
            available_count=int(record.citation_available_count or 0),
        )
    return AnswerGeneration(
        mode=record.generation_mode or "model",
        degraded=_record_status_value(record.status) == "degraded",
        model_name=record.model_name,
        failure_reason=record.failure_reason,
        failure_message=record.failure_message,
        failure_detail=record.failure_detail,
        citation_check=citation_check,
    )


def _record_status_value(status: Any) -> str:
    return str(getattr(status, "value", status) or "")


def build_research_detail(record) -> ResearchRecordDetail:
    """Map an ORM record onto the detail schema without touching other services."""
    return ResearchRecordDetail(
        id=record.id,
        mode=str(getattr(record.mode, "value", record.mode)),
        question=record.question,
        answer=record.answer or "",
        status=_record_status_value(record.status),
        evidence_sufficient=bool(record.evidence_sufficient),
        generation=build_generation_from_record(record),
        intent=record.intent,
        sources=[RagSourceEvidence(**item) for item in (record.sources or [])],
        entities=[RagEntityReference(**item) for item in (record.entities or [])],
        relationships=[RagRelationshipReference(**item) for item in (record.relationships or [])],
        warnings=list(record.warnings or []),
        elapsed_ms=record.elapsed_ms,
        group_id=getattr(record, "group_id", None),
        group_name=getattr(record, "group_name", None),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def build_research_summary(record) -> ResearchRecordSummary:
    """Map an ORM record onto one list row."""
    return ResearchRecordSummary(
        id=record.id,
        mode=str(getattr(record.mode, "value", record.mode)),
        question=record.question,
        status=_record_status_value(record.status),
        evidence_sufficient=bool(record.evidence_sufficient),
        generation_mode=record.generation_mode,
        failure_reason=record.failure_reason,
        citation_valid=record.citation_valid,
        source_count=len(record.sources or []),
        elapsed_ms=record.elapsed_ms,
        group_id=getattr(record, "group_id", None),
        group_name=getattr(record, "group_name", None),
        created_at=record.created_at,
    )
