"""Pydantic schemas for research groups and their batch import."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.research_group import GroupImportStatus


class GroupCreate(BaseModel):
    """Create a document group."""

    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    #: Whether this group takes part in automatic research (deployment switch still applies).
    auto_extract: Optional[bool] = None


class GroupUpdate(BaseModel):
    """Rename, re-describe or toggle automatic research for a group."""

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    auto_extract: Optional[bool] = None


class AutoResearchStatusResponse(BaseModel):
    """Why automatic research will or will not run for this group."""

    global_enabled: bool = False
    group_enabled: bool = False
    model_configured: bool = False
    budget_blocks: int = 0
    budget_tokens: Optional[int] = None
    model: Optional[str] = None
    effective: bool = Field(
        False, description="True only when a new import would really be queued"
    )
    reason: str = Field(
        ...,
        description=(
            "started | global_disabled | group_disabled | model_not_configured"
        ),
    )
    detail: str = Field(..., description="User-facing explanation, never a fake status")


class AutoExtractionOutcomeResponse(BaseModel):
    """What happened to one document when auto research was attempted."""

    document_id: Optional[int] = None
    started: bool = False
    reason: str = Field(
        ...,
        description=(
            "started | already_active | already_extracted | global_disabled | "
            "group_disabled | model_not_configured | no_text | conflict | error"
        ),
    )
    detail: str
    run_id: Optional[int] = None
    dispatch_state: Optional[str] = None
    dispatch_error: Optional[str] = None
    total_blocks: int = 0


class GroupResponse(BaseModel):
    """Group header with membership count."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    auto_extract: bool = True
    document_count: int = 0
    ready_count: int = 0
    failed_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None


class GroupListResponse(BaseModel):
    items: List[GroupResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class GroupMemberDocument(BaseModel):
    """One document inside one group, with its real processing status."""

    document_id: int
    filename: str
    original_filename: Optional[str] = None
    file_type: str
    file_size: int = 0
    page_count: int = 0
    block_count: int = 0
    document_status: str
    import_status: GroupImportStatus
    import_error: Optional[str] = None
    last_batch_id: Optional[str] = None
    added_at: Optional[datetime] = None


class GroupDetailResponse(BaseModel):
    group: GroupResponse
    documents: List[GroupMemberDocument] = Field(default_factory=list)
    warning: Optional[str] = None
    auto_research: Optional[AutoResearchStatusResponse] = None


class GroupDocumentIdsRequest(BaseModel):
    """Attach already-uploaded documents to a group."""

    document_ids: List[int] = Field(..., min_length=1, max_length=200)


class GroupMembershipChangeResponse(BaseModel):
    added: List[int] = Field(default_factory=list)
    duplicates: List[int] = Field(default_factory=list)
    missing: List[int] = Field(default_factory=list)
    group: Optional[GroupResponse] = None
    auto_research: List[AutoExtractionOutcomeResponse] = Field(default_factory=list)


class ImportBatchItem(BaseModel):
    filename: str
    document_id: Optional[int] = None
    duplicate: bool = False
    created_document: bool = False
    import_status: Optional[GroupImportStatus] = None
    error: Optional[str] = None
    auto_research: Optional[AutoExtractionOutcomeResponse] = None


class ImportBatchResponse(BaseModel):
    """Result of one batch upload into a group."""

    batch_id: str
    group_id: int
    accepted_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    items: List[ImportBatchItem] = Field(default_factory=list)
    auto_research: List[AutoExtractionOutcomeResponse] = Field(default_factory=list)


class ImportBatchStatusResponse(BaseModel):
    """Per-document status of one batch (safe to poll)."""

    batch_id: str
    group_id: int
    total: int = 0
    status_counts: Dict[str, int] = Field(default_factory=dict)
    items: List[ImportBatchItem] = Field(default_factory=list)
