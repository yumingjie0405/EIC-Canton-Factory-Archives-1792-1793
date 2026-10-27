"""Pydantic schemas for Knowledge Graph entities, mentions, relationships, authorities, and metrics."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.kg.enums import (
    AuthoritySource,
    ClaimStatus,
    Directionality,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)


# ==========================================
# Helpers & Validators
# ==========================================

def _strip_str(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = v.strip()
    return s if s else None


def _validate_confidence(v: Optional[float]) -> Optional[float]:
    if v is not None and not (0.0 <= v <= 1.0):
        raise ValueError("Confidence must be between 0.0 and 1.0 inclusive")
    return v


# ==========================================
# Person Schemas
# ==========================================

class PersonBase(BaseModel):
    """Base schema for Person."""
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    display_name: str = Field(..., min_length=1, description="Primary name for UI display")
    aliases: Optional[List[str]] = None
    primary_role: Optional[str] = None
    organization: Optional[str] = None
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    active_start: Optional[date] = None
    active_end: Optional[date] = None
    base_location: Optional[str] = None
    biographical_notes: Optional[str] = None
    identity_status: IdentityStatus = IdentityStatus.PROVISIONAL
    review_status: ReviewStatusKG = ReviewStatusKG.UNREVIEWED

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("display_name cannot be empty or blank")
        return s

    @field_validator(
        "canonical_name_en",
        "canonical_name_zh",
        "primary_role",
        "organization",
        "base_location",
        "biographical_notes",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, v: Any) -> Any:
        return _strip_str(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def validate_person_dates(self) -> "PersonBase":
        if self.birth_year is not None and self.death_year is not None:
            if self.birth_year > self.death_year:
                raise ValueError(
                    f"birth_year ({self.birth_year}) cannot be greater than death_year ({self.death_year})"
                )
        if self.active_start is not None and self.active_end is not None:
            if self.active_start > self.active_end:
                raise ValueError(
                    f"active_start ({self.active_start}) cannot be after active_end ({self.active_end})"
                )
        return self


class PersonCreate(PersonBase):
    """Schema for creating a Person."""
    pass


class PersonUpdate(BaseModel):
    """Schema for updating a Person."""
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    display_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    primary_role: Optional[str] = None
    organization: Optional[str] = None
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    active_start: Optional[date] = None
    active_end: Optional[date] = None
    base_location: Optional[str] = None
    biographical_notes: Optional[str] = None
    identity_status: Optional[IdentityStatus] = None
    review_status: Optional[ReviewStatusKG] = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("display_name cannot be empty or blank")
            return s
        return v

    @model_validator(mode="after")
    def validate_update_dates(self) -> "PersonUpdate":
        if self.birth_year is not None and self.death_year is not None:
            if self.birth_year > self.death_year:
                raise ValueError("birth_year cannot be greater than death_year")
        if self.active_start is not None and self.active_end is not None:
            if self.active_start > self.active_end:
                raise ValueError("active_start cannot be after active_end")
        return self


class PersonResponse(BaseModel):
    """Schema for returning Person details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    display_name: str
    aliases: Optional[Any] = None
    primary_role: Optional[str] = None
    organization: Optional[str] = None
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    active_start: Optional[date] = None
    active_end: Optional[date] = None
    base_location: Optional[str] = None
    biographical_notes: Optional[str] = None
    identity_status: IdentityStatus
    review_status: ReviewStatusKG
    created_at: datetime
    updated_at: datetime


class PersonListResponse(BaseModel):
    """Schema for Person list query response."""
    items: List[PersonResponse]
    total: int


# ==========================================
# PersonMention Schemas
# ==========================================

class PersonMentionBase(BaseModel):
    """Base schema for PersonMention."""
    block_id: int
    person_id: Optional[int] = None
    raw_text: str = Field(..., min_length=1, description="Original archival mention string")
    normalized_text: Optional[str] = None
    mention_type: MentionType = MentionType.PERSON_NAME
    temporal_date: Optional[date] = None
    temporal_year: Optional[int] = None
    spatial_location: Optional[str] = None
    confidence: Optional[float] = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    review_status: ReviewStatusKG = ReviewStatusKG.UNREVIEWED
    candidate_person_ids: Optional[List[int]] = None
    evidence_text: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("raw_text cannot be empty or blank")
        return s

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: Optional[float]) -> Optional[float]:
        return _validate_confidence(v)

    @field_validator("normalized_text", "spatial_location", "evidence_text", mode="before")
    @classmethod
    def strip_strings(cls, v: Any) -> Any:
        return _strip_str(v) if isinstance(v, str) else v


class PersonMentionCreate(PersonMentionBase):
    """Schema for creating a PersonMention."""
    pass


class PersonMentionUpdate(BaseModel):
    """Schema for updating a PersonMention."""
    person_id: Optional[int] = None
    normalized_text: Optional[str] = None
    mention_type: Optional[MentionType] = None
    temporal_date: Optional[date] = None
    temporal_year: Optional[int] = None
    spatial_location: Optional[str] = None
    confidence: Optional[float] = None
    resolution_status: Optional[ResolutionStatus] = None
    review_status: Optional[ReviewStatusKG] = None
    candidate_person_ids: Optional[List[int]] = None
    evidence_text: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: Optional[float]) -> Optional[float]:
        return _validate_confidence(v)


class PersonMentionResponse(BaseModel):
    """Schema for returning PersonMention details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    block_id: int
    person_id: Optional[int] = None
    raw_text: str
    normalized_text: Optional[str] = None
    mention_type: MentionType
    temporal_date: Optional[date] = None
    temporal_year: Optional[int] = None
    spatial_location: Optional[str] = None
    confidence: Optional[float] = None
    resolution_status: ResolutionStatus
    review_status: ReviewStatusKG
    candidate_person_ids: Optional[Any] = None
    evidence_text: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class PersonMentionListResponse(BaseModel):
    """Schema for PersonMention list query response."""
    items: List[PersonMentionResponse]
    total: int


# ==========================================
# Relationship & Evidence Schemas
# ==========================================

class RelationshipEvidenceCreate(BaseModel):
    """Schema for creating a relationship evidence link."""
    relationship_id: Optional[int] = None
    person_mention_id: Optional[int] = None
    block_id: int
    evidence_role: EvidenceRole = EvidenceRole.SUPPORTS
    quote_text: Optional[str] = None


class RelationshipEvidenceResponse(BaseModel):
    """Schema for returning relationship evidence details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    relationship_id: int
    person_mention_id: Optional[int] = None
    block_id: int
    evidence_role: EvidenceRole
    quote_text: Optional[str] = None
    created_at: datetime


class RelationshipBase(BaseModel):
    """Base schema for Relationship."""
    source_person_id: int
    target_person_id: int
    relation_type: RelationType = RelationType.OTHER
    relation_label: Optional[str] = None
    directionality: Directionality = Directionality.DIRECTED
    weight: Optional[float] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    time_description: Optional[str] = None
    spatial_location: Optional[str] = None
    evidence_summary: Optional[str] = None
    confidence: Optional[float] = None
    review_status: ReviewStatusKG = ReviewStatusKG.UNREVIEWED
    claim_status: ClaimStatus = ClaimStatus.ASSERTED

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: Optional[float]) -> Optional[float]:
        return _validate_confidence(v)

    @field_validator("weight")
    @classmethod
    def check_weight(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0.0:
            raise ValueError("weight must be non-negative (>= 0)")
        return v

    @model_validator(mode="after")
    def validate_relationship_dates(self) -> "RelationshipBase":
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from > self.valid_to:
                raise ValueError(
                    f"valid_from ({self.valid_from}) cannot be after valid_to ({self.valid_to})"
                )
        return self


class RelationshipCreate(RelationshipBase):
    """Schema for creating a Relationship."""
    pass


class RelationshipUpdate(BaseModel):
    """Schema for updating a Relationship."""
    relation_type: Optional[RelationType] = None
    relation_label: Optional[str] = None
    directionality: Optional[Directionality] = None
    weight: Optional[float] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    time_description: Optional[str] = None
    spatial_location: Optional[str] = None
    evidence_summary: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[ReviewStatusKG] = None
    claim_status: Optional[ClaimStatus] = None

    @field_validator("confidence")
    @classmethod
    def check_confidence(cls, v: Optional[float]) -> Optional[float]:
        return _validate_confidence(v)

    @field_validator("weight")
    @classmethod
    def check_weight(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0.0:
            raise ValueError("weight must be non-negative (>= 0)")
        return v

    @model_validator(mode="after")
    def validate_update_dates(self) -> "RelationshipUpdate":
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from > self.valid_to:
                raise ValueError("valid_from cannot be after valid_to")
        return self


class RelationshipResponse(BaseModel):
    """Schema for returning Relationship details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_person_id: int
    target_person_id: int
    relation_type: RelationType
    relation_label: Optional[str] = None
    directionality: Directionality
    weight: Optional[float] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    time_description: Optional[str] = None
    spatial_location: Optional[str] = None
    evidence_summary: Optional[str] = None
    confidence: Optional[float] = None
    review_status: ReviewStatusKG
    claim_status: ClaimStatus
    created_at: datetime
    updated_at: datetime
    evidences: Optional[List[RelationshipEvidenceResponse]] = None


class RelationshipListResponse(BaseModel):
    """Schema for Relationship list query response."""
    items: List[RelationshipResponse]
    total: int


# ==========================================
# ExternalAuthority Schemas
# ==========================================

class ExternalAuthorityBase(BaseModel):
    """Base schema for ExternalAuthority."""
    person_id: int
    authority_source: AuthoritySource
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    record_title: Optional[str] = None
    historical_notes: Optional[str] = None
    match_confidence: Optional[float] = None
    review_status: ReviewStatusKG = ReviewStatusKG.UNREVIEWED
    claim_status: ClaimStatus = ClaimStatus.ACCEPTED
    accessed_at: Optional[datetime] = None

    @field_validator("match_confidence")
    @classmethod
    def check_match_confidence(cls, v: Optional[float]) -> Optional[float]:
        return _validate_confidence(v)


class ExternalAuthorityCreate(ExternalAuthorityBase):
    """Schema for creating an ExternalAuthority record."""
    pass


class ExternalAuthorityUpdate(BaseModel):
    """Schema for updating an ExternalAuthority record."""
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    record_title: Optional[str] = None
    historical_notes: Optional[str] = None
    match_confidence: Optional[float] = None
    review_status: Optional[ReviewStatusKG] = None
    claim_status: Optional[ClaimStatus] = None
    accessed_at: Optional[datetime] = None

    @field_validator("match_confidence")
    @classmethod
    def check_match_confidence(cls, v: Optional[float]) -> Optional[float]:
        return _validate_confidence(v)


class ExternalAuthorityResponse(BaseModel):
    """Schema for returning ExternalAuthority record details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    authority_source: AuthoritySource
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    record_title: Optional[str] = None
    historical_notes: Optional[str] = None
    match_confidence: Optional[float] = None
    review_status: ReviewStatusKG
    claim_status: ClaimStatus
    accessed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# ==========================================
# SNAGraphMetric Schemas
# ==========================================

class SNAGraphMetricResponse(BaseModel):
    """Schema for returning SNA calculation metric snapshot."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    run_id: str
    snapshot_at: datetime
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    graph_scope: str
    degree_centrality: Optional[float] = None
    betweenness_centrality: Optional[float] = None
    closeness_centrality: Optional[float] = None
    pagerank: Optional[float] = None
    community_id: Optional[str] = None
    algorithm_version: str
    parameters: Optional[Dict[str, Any]] = None
    created_at: datetime


# ==========================================
# Extended Person & Relationship Review Schemas
# ==========================================

class PersonDetailResponse(PersonResponse):
    """Schema for detailed Person profile including external authorities."""
    external_authorities: Optional[List[ExternalAuthorityResponse]] = None


class RelationshipReviewRequest(BaseModel):
    """Schema for reviewing and adjusting historical relationship confidence and claim status."""
    review_status: ReviewStatusKG = ReviewStatusKG.REVIEWED
    claim_status: Optional[ClaimStatus] = None
    reviewer: Optional[str] = None
    evidence_notes: Optional[str] = None

