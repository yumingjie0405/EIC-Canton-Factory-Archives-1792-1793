"""Pydantic schemas for Stage 3 disambiguation, review, external authorities, and entity merges."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CandidateMatchResponse(BaseModel):
    """Evaluation breakdown of a single candidate Person match for a mention."""
    person_id: int
    display_name: str
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    primary_role: Optional[str] = None
    preliminary_similarity: float
    name_score: float
    temporal_score: float
    spatial_score: float
    role_score: float
    context_score: float
    authority_score: float
    total_score: float
    supporting_evidence: List[str] = Field(default_factory=list)
    contradiction_flags: List[str] = Field(default_factory=list)
    recommendation: str  # 'confirm', 'needs_review', 'reject'


class MentionDisambiguationEvaluationResponse(BaseModel):
    """Response containing a mention and its evaluated candidate Persons."""
    mention_id: int
    raw_text: str
    normalized_text: Optional[str] = None
    temporal_year: Optional[int] = None
    spatial_location: Optional[str] = None
    current_resolution_status: str
    candidates_count: int
    candidates: List[CandidateMatchResponse]


class ConfirmResolutionRequest(BaseModel):
    """Payload to confirm an association between a PersonMention and a Person."""
    person_id: int
    reviewer: str = Field("curator", description="Name/identifier of human reviewer")
    reason: Optional[str] = Field(None, description="Historiographical rationale or reference")


class ReviewActionRequest(BaseModel):
    """Payload for rejecting or deferring resolution."""
    person_id: Optional[int] = None
    reviewer: str = Field("curator", description="Reviewer name")
    reason: Optional[str] = None


class LinkAuthorityRequest(BaseModel):
    """Payload to associate a Person with an external historiographical authority."""
    authority_source: str = Field(..., description="e.g. CBDB, IOR, Liang_Canton, Wikidata")
    external_id: Optional[str] = None
    record_title: Optional[str] = None
    external_url: Optional[str] = None
    historical_notes: Optional[str] = None
    confidence: float = Field(0.95, ge=0.0, le=1.0)


class MergePreviewResponse(BaseModel):
    """Pre-merge conflict and impact analysis report."""
    primary_person: Dict[str, Any]
    secondary_person: Dict[str, Any]
    impact: Dict[str, int]
    potential_conflicts: List[str]
    can_merge: bool


class MergeEntitiesRequest(BaseModel):
    """Payload to execute atomic entity merge."""
    primary_person_id: int
    secondary_person_id: int
    operator: str = Field("curator", description="Operator performing merge")
    reason: Optional[str] = None


class SplitMergeRequest(BaseModel):
    """Payload to reversibly undo a previous merge using its audit log ID."""
    audit_id: int
    operator: str = Field("curator", description="Operator performing split/undo")
    reason: Optional[str] = None


class AuditRecordResponse(BaseModel):
    """Audit trail record for merge, split, or resolution review."""
    id: int
    operation_type: str
    primary_person_id: int
    secondary_person_id: Optional[int] = None
    snapshot_details: Dict[str, Any]
    reason: Optional[str] = None
    operator: str
    is_reverted: bool
    created_at: datetime
