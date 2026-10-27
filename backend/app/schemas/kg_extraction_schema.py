"""Pydantic schemas for LLM-based entity and relationship extraction."""
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExtractedEntity(BaseModel):
    """An entity mention extracted by LLM from archival text."""
    mention: str = Field(..., min_length=1, description="Raw archival mention string")
    normalized_name: Optional[str] = None
    role: Optional[str] = None
    organization: Optional[str] = None
    mention_type: str = Field("person_name", description="person_name, title, alias, abbreviation")
    confidence: float = Field(0.9, ge=0.0, le=1.0)
    evidence: Optional[str] = None

    @field_validator("mention")
    @classmethod
    def clean_mention(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("mention cannot be empty or whitespace only")
        return s

    @field_validator("normalized_name", "role", "organization", "evidence", mode="before")
    @classmethod
    def strip_optional_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v


class ExtractedRelationship(BaseModel):
    """A relationship extracted by LLM between two person mentions."""
    source_mention: str = Field(..., min_length=1, description="Source person mention string")
    target_mention: str = Field(..., min_length=1, description="Target person mention string")
    relation_type: str = Field("other", description="commercial, diplomatic, guarantee, debt, etc.")
    relation_label: Optional[str] = None
    directionality: str = Field("directed", description="directed or undirected")
    weight: Optional[float] = Field(None, ge=0.0)
    confidence: float = Field(0.85, ge=0.0, le=1.0)
    evidence: Optional[str] = None

    @field_validator("source_mention", "target_mention")
    @classmethod
    def clean_mentions(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("relationship source/target mention cannot be empty")
        return s

    @field_validator("relation_type")
    @classmethod
    def clean_relation_type(cls, v: str) -> str:
        s = v.strip().lower()
        allowed = {
            "commercial", "diplomatic", "guarantee", "debt",
            "correspondence", "kinship", "accompaniment",
            "employment", "translation", "other"
        }
        return s if s in allowed else "other"


class KGExtractionResult(BaseModel):
    """Structured extraction output returned by LLM and validated by Pydantic."""
    entities: List[ExtractedEntity] = Field(default_factory=list)
    relationships: List[ExtractedRelationship] = Field(default_factory=list)
    temporal_date_hint: Optional[str] = None
    spatial_location_hint: Optional[str] = None


class ExtractionTaskMetrics(BaseModel):
    """Observability metrics tracking extraction runtime and LLM token usage."""
    task_id: Optional[str] = None
    block_id: Optional[int] = None
    page_id: Optional[int] = None
    document_id: Optional[int] = None
    model_name: str
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    entities_count: int = 0
    relationships_count: int = 0
    error_message: Optional[str] = None


class BlockExtractionResponse(BaseModel):
    """Response returned upon completing block-level entity extraction."""
    block_id: int
    entities_extracted: int
    relationships_extracted: int
    entities: List[ExtractedEntity]
    relationships: List[ExtractedRelationship]
    metrics: ExtractionTaskMetrics


class BatchExtractionRequest(BaseModel):
    """Request payload for triggering batch entity extraction."""
    page_id: Optional[int] = None
    document_id: Optional[int] = None
    overwrite_existing: bool = Field(False, description="Whether to re-extract and overwrite existing mentions")
