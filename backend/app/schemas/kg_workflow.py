"""Schemas for the review workflow: relationship review, disambiguation, snapshots."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RelationshipReviewRequestModel(BaseModel):
    """One human decision about a claim. Statuses mirror the stored enums exactly."""

    claim_status: Optional[str] = Field(
        None, description="asserted | accepted | uncertain | contested | rejected"
    )
    review_status: Optional[str] = Field(None, description="unreviewed | in_review | reviewed")
    evidence_note: Optional[str] = Field(None, max_length=1000)
    reason: Optional[str] = Field(None, max_length=500)
    reviewer: Optional[str] = Field(None, max_length=100)


class GroupRelationshipReviewListResponse(BaseModel):
    """Evidence-first candidate list with unpaginated counts."""

    group_id: int
    group_name: str
    total: int = 0
    skip: int = 0
    limit: int = 50
    items: List[Dict[str, Any]] = Field(default_factory=list)
    counts: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    data_trust: Optional[Dict[str, Any]] = None


class RelationshipReviewResponse(BaseModel):
    relationship: Dict[str, Any]
    audit_id: int
    source_kind: str
    source_kind_label: str
    synthetic: bool = False
    unverified: bool = False
    warning: Optional[str] = None


class MentionClusterListResponse(BaseModel):
    group_id: int
    group_name: str
    total_mentions: int = 0
    total_clusters: int = 0
    items: List[Dict[str, Any]] = Field(default_factory=list)
    data_trust: Optional[Dict[str, Any]] = None


class BatchLinkPreviewRequest(BaseModel):
    normalized_text: str = Field(..., min_length=1, max_length=500)
    person_id: Optional[int] = Field(None, ge=1)
    new_person_name: Optional[str] = Field(None, max_length=200)


class BatchLinkConfirmRequest(BatchLinkPreviewRequest):
    confirm_token: str = Field(..., min_length=8, description="Token returned by the preview")
    reason: Optional[str] = Field(None, max_length=500)
    operator: Optional[str] = Field(None, max_length=100)


class BatchLinkPreviewResponse(BaseModel):
    group_id: int
    group_name: str
    normalized_text: str
    target_person_id: Optional[int] = None
    target_person_name: str
    create_new_person: bool
    mention_count: int
    mention_ids: List[int] = Field(default_factory=list)
    years: List[int] = Field(default_factory=list)
    source_kinds: Dict[str, int] = Field(default_factory=dict)
    synthetic: bool = False
    warnings: List[str] = Field(default_factory=list)
    confirm_token: str


class BatchLinkConfirmResponse(BaseModel):
    batch_id: str
    person_id: int
    person_name: str
    created_person: bool
    mentions_linked: int
    synthetic: bool = False
    source_kinds: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    audit_ids: List[int] = Field(default_factory=list)


class SnapshotCreateRequest(BaseModel):
    run_id: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = Field(None, max_length=200)
    note: Optional[str] = Field(None, max_length=1000)
    group_id: int = Field(..., ge=1)
    view: str = Field("all", pattern="^(official|candidates|all)$")
    filters: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = Field(None, max_length=100)


class SnapshotSummary(BaseModel):
    run_id: str
    name: Optional[str] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    view: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    capability: Optional[str] = None
    synthetic: bool = False
    created_by: Optional[str] = None
    created_at: Optional[str] = None


class SnapshotDetail(SnapshotSummary):
    note: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    algorithm_version: Optional[str] = None
    persons: List[Dict[str, Any]] = Field(default_factory=list)
    relationships: List[Dict[str, Any]] = Field(default_factory=list)
    metrics_summary: Optional[Dict[str, Any]] = None
    excluded: Optional[Dict[str, Any]] = None
    data_trust: Optional[Dict[str, Any]] = None
    extraction_run_ids: List[int] = Field(default_factory=list)
    capabilities: Optional[Dict[str, Any]] = None


class SnapshotCompareResponse(BaseModel):
    left: SnapshotSummary
    right: SnapshotSummary
    parameters_changed: bool = False
    view_changed: bool = False
    structure: Dict[str, List[int]] = Field(default_factory=dict)
    review_changes: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_changes: List[Dict[str, Any]] = Field(default_factory=list)
    provenance_changes: List[Dict[str, Any]] = Field(default_factory=list)
    provenance_summary: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
