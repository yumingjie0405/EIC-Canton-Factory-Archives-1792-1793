"""Read-only schemas for the group-scoped person graph.

The group graph is *derived*: nodes come from in-group person mentions or from
relationships with in-group evidence, and every relationship must carry at least
one in-group evidence block. These payloads therefore always describe the scope
they were computed for, so a client cannot mistake a group view for the global one.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class KGExtractionRunStatusResponse(BaseModel):
    """Progress/state of one persistent extraction run."""

    run_id: int
    scope_type: str
    scope_id: int
    group_id: Optional[int] = None
    document_id: Optional[int] = None
    status: str = Field(
        ...,
        description=(
            "queued | running | paused_budget | paused_error | paused_format | "
            "paused_manual | completed | partial | failed"
        ),
    )
    model: Optional[str] = None
    is_mock_model: bool = Field(
        False, description="True when the run used a local/mock model (output is not evidence)"
    )
    source_kind: Optional[str] = Field(
        None,
        description=(
            "provider_reported | historical_seed | mock_text_derived | mock_constant | unknown"
        ),
    )
    source_kind_label: Optional[str] = None
    token_source: Optional[str] = Field(
        None,
        description=(
            "provider | estimated | unknown — what `tokens_used` actually measures. "
            "Mock/stub counters are never a supplier bill."
        ),
    )
    token_source_label: Optional[str] = None
    prompt_hash: Optional[str] = Field(
        None, description="Fingerprint of prompt template + extraction config"
    )
    trigger: str = Field(
        "manual",
        description="manual | auto_import | auto_attach | recovery — why this run exists",
    )
    dispatch_state: str = Field(
        "pending",
        description=(
            "pending | dispatched | failed — whether the broker actually accepted the "
            "message; a failed run stays in the ledger and can be re-delivered"
        ),
    )
    dispatch_attempts: int = 0
    dispatch_error: Optional[str] = None
    total_blocks: int = 0
    processed_blocks: int = 0
    skipped_blocks: int = 0
    failed_blocks: int = 0
    attempted_blocks: int = 0
    progress_percent: float = 0.0
    tokens_used: int = 0
    budget_blocks: Optional[int] = None
    budget_tokens: Optional[int] = None
    budget_source: Optional[str] = Field(
        None,
        description="explicit | default | auto — how the ceiling was chosen (NULL = historical run)",
    )
    pause_reason: Optional[str] = None
    reset_at: Optional[str] = Field(
        None, description="When the derived records of this run were cleaned up (run row kept)"
    )
    reset_reason: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class KGExtractionRunsSummaryResponse(BaseModel):
    """The group's runs, classified — so a paused attempt cannot mask a finished one."""

    active_run: Optional[KGExtractionRunStatusResponse] = None
    paused_run: Optional[KGExtractionRunStatusResponse] = None
    last_finished_run: Optional[KGExtractionRunStatusResponse] = None
    failed_run: Optional[KGExtractionRunStatusResponse] = None
    primary_run: Optional[KGExtractionRunStatusResponse] = Field(
        None, description="active > paused > last finished > failed (what the UI headlines)"
    )
    recent_runs: List[KGExtractionRunStatusResponse] = Field(default_factory=list)


class GroupGraphScopeResponse(BaseModel):
    """Coverage / extraction state of one document group."""

    group_id: int
    group_name: str
    document_count: int = Field(..., description="Documents currently in the group")
    block_count: int = Field(..., description="Text blocks forming the group's evidence domain")
    blocks_with_mentions: int = Field(..., description="Blocks that already yield person mentions")
    blocks_with_extraction: int = Field(
        0, description="Blocks that yield mentions or relationship evidence (processed)"
    )
    mention_count: int = 0
    pending_mention_reviews: int = 0
    person_count: int = Field(0, description="Persons referenced by in-group evidence")
    relationship_count_official: int = Field(0, description="Relationships with accepted claim status")
    relationship_count_candidates: int = Field(0, description="Relationships still awaiting acceptance")
    extraction_state: str = Field(
        ...,
        description=(
            "no_documents | no_text | not_extracted | queued_extraction | extracting | "
            "paused_extraction | extraction_failed | partial_extraction | "
            "extracted_empty | ready"
        ),
    )
    state_message: str
    task_tracking_available: bool = Field(
        True,
        description=(
            "True because the extraction ledger (kg_extraction_runs) exists: progress, "
            "pause reasons and token spend are read from durable rows, not from memory."
        ),
    )
    extraction_run: Optional["KGExtractionRunStatusResponse"] = Field(
        None, description="Headline run (active > paused > last finished)"
    )
    extraction_runs: Optional["KGExtractionRunsSummaryResponse"] = Field(
        None,
        description=(
            "All recent runs classified by role: live work, a paused attempt and the last "
            "finished run are different facts and are reported separately"
        ),
    )
    data_trust: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Provenance of the stored records: mock-derived runs are reported so their "
            "output is never presented as archival evidence"
        ),
    )
    auto_research: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Automatic-research configuration for this group: enabled/disabled and the "
            "exact reason, plus the per-run budgets"
        ),
    )


class GroupGraphPersonItem(BaseModel):
    """A person node inside the group graph."""

    id: int
    display_name: str
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    primary_role: Optional[str] = None
    organization: Optional[str] = None
    identity_status: str
    role_source: Optional[str] = Field(
        None,
        description=(
            "model_extracted | human_verified | heuristic | unknown. NULL means no role "
            "was ever recorded — which must be shown as 未知, not as 'other'."
        ),
    )
    role_category: str = Field(
        "unknown",
        description="Normalised category used for colouring (see role_taxonomy)",
    )


class GroupGraphRelationshipItem(BaseModel):
    """A relationship edge inside the group graph, with its in-group evidence count."""

    id: int
    source_person_id: int
    target_person_id: int
    relation_type: str
    relation_label: Optional[str] = None
    directionality: Optional[str] = None
    claim_status: str
    review_status: str
    confidence: Optional[float] = None
    time_description: Optional[str] = None
    evidence_count: int = Field(
        0,
        description=(
            "Number of *distinct in-group blocks* supporting this claim. This is an "
            "archival-locator count, not a count of independent historical facts: one "
            "claim repeated in 6 000 blocks is still one claim."
        ),
    )
    evidence_rows: int = Field(
        0, description="Stored evidence rows in this group (usually >= evidence_count)"
    )
    distinct_quotes: int = Field(
        0, description="Number of distinct quote texts behind this claim in this group"
    )
    verified_evidence_count: int = Field(
        0, description="In-group evidence rows with provider/seed provenance"
    )
    synthetic_evidence_count: int = Field(
        0, description="In-group evidence rows produced by mock/stub models"
    )
    unknown_evidence_count: int = Field(
        0, description="In-group evidence rows whose origin could not be reconstructed"
    )
    evidence_block_ids: List[int] = Field(default_factory=list)
    source_kind: Optional[str] = Field(
        None,
        description=(
            "Origin of the supporting evidence: provider_reported / historical_seed / "
            "mock_text_derived / mock_constant / unknown. Independent of claim_status."
        ),
    )
    source_kind_label: Optional[str] = None
    source_kinds: Dict[str, int] = Field(default_factory=dict)
    synthetic: bool = Field(
        False, description="True when any supporting evidence is mock/stub derived"
    )


class GroupGraphOverviewResponse(BaseModel):
    """Complete node/edge set of the group subgraph (never truncated)."""

    group_id: int
    group_name: str
    view: str = Field(..., description="official | candidates | all")
    persons: List[GroupGraphPersonItem] = Field(default_factory=list)
    relationships: List[GroupGraphRelationshipItem] = Field(default_factory=list)
    scope_person_count: int = 0
    scope_relationship_count: int = 0
    role_counts: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Persons per normalised role category **in this subgraph**; the UI must only "
            "offer legend entries that actually occur here. 'unknown' is counted, never "
            "merged into 'other'."
        ),
    )
    role_known_count: int = Field(
        0, description="Persons whose role category is not 'unknown'"
    )
    role_unknown_count: int = Field(0, description="Persons with no usable role wording")
    role_source_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Persons per role provenance (model_extracted / human_verified / heuristic / unknown)",
    )
    evidence_rows_total: int = Field(
        0, description="In-group evidence rows behind every edge in this subgraph"
    )
    verified_evidence_rows: int = 0
    synthetic_evidence_rows: int = 0
    unknown_evidence_rows: int = 0
    evidence_note: str = Field(
        "",
        description=(
            "Human-readable explanation of what the evidence counters mean, so a large "
            "row count is never mistaken for a large number of archival findings"
        ),
    )


class GroupEvidenceItem(BaseModel):
    """One archival evidence row: never merged with other rows into a single quote."""

    evidence_id: int
    document_id: int
    document_name: str
    page_number: int
    block_id: int
    block_index: int
    block_label: Optional[str] = None
    quote_text: Optional[str] = None
    block_text: Optional[str] = None
    evidence_role: str
    source_kind: Optional[str] = None
    source_kind_label: Optional[str] = None
    extraction_run_id: Optional[int] = None
    synthetic: bool = False


class GroupRelationshipEvidenceResponse(BaseModel):
    group_id: int
    relationship_id: int
    source_person_id: int
    target_person_id: int
    relation_type: str
    claim_status: str
    review_status: str
    items: List[GroupEvidenceItem] = Field(default_factory=list)
    out_of_group_evidence_count: int = Field(
        0, description="Evidence rows that exist globally but outside this group"
    )
    in_group_evidence_count: int = Field(
        0, description="Total in-group evidence rows (items may be capped)"
    )
    distinct_block_count: int = Field(
        0, description="Distinct in-group blocks behind this claim (independent locators)"
    )
    distinct_quote_count: int = Field(0, description="Distinct quote texts behind this claim")
    source_kind_counts: Dict[str, int] = Field(
        default_factory=dict, description="In-group evidence rows per provenance kind"
    )
    verified_evidence_count: int = 0
    synthetic_evidence_count: int = 0
    unknown_evidence_count: int = 0
    truncated: bool = Field(False, description="True when items < in_group_evidence_count")


class GroupPersonMentionItem(BaseModel):
    mention_id: int
    raw_text: str
    normalized_text: Optional[str] = None
    mention_type: str
    resolution_status: str
    review_status: str
    temporal_year: Optional[int] = None
    document_id: int
    document_name: str
    page_number: int
    block_id: int
    block_index: int
    evidence_text: Optional[str] = None
    linked: bool = Field(
        True,
        description=(
            "False for a mention that carries the same name but is not yet linked to "
            "this person (identity linking is still a reviewer decision)"
        ),
    )


class GroupPersonMentionsResponse(BaseModel):
    group_id: int
    person_id: int
    display_name: str
    identity_status: str
    items: List[GroupPersonMentionItem] = Field(default_factory=list)
    in_group_mention_count: int = Field(
        0, description="Total in-group mentions (items may be capped)"
    )
    unlinked_name_match_count: int = Field(
        0,
        description=(
            "In-group mentions with the same normalized name that are not linked to this "
            "person yet — evidence the person appears in the archive, not a claim that "
            "the identification is confirmed"
        ),
    )
    unlinked_items: List[GroupPersonMentionItem] = Field(
        default_factory=list,
        description="Up to `limit` of the same-name unlinked mentions, for display",
    )
    truncated: bool = Field(False, description="True when items < in_group_mention_count")


class FacetCountItem(BaseModel):
    """One selectable filter value with the number of relationships it would show."""

    value: str
    count: int = 0


class FacetYearItem(BaseModel):
    """One relationship year (from relationship dates, never from mention dates)."""

    year: int
    count: int = 0


class GraphFacetsResponse(BaseModel):
    """Filter metadata that is guaranteed to describe the same subgraph as the overview."""

    group_id: int
    group_name: str
    view: str = Field(..., description="official | candidates | all")
    relation_types: List[FacetCountItem] = Field(default_factory=list)
    dated_years: List[FacetYearItem] = Field(default_factory=list)
    year_range: Optional[Dict[str, int]] = None
    undated_relationship_count: int = 0
    total_relationship_count: int = 0
    time_filter_available: bool = False
    time_filter_reason: Optional[str] = Field(
        None, description="Why the year filter cannot be used (None when it can)"
    )
    include_unknown_dates: bool = True
    mention_years: List[FacetYearItem] = Field(
        default_factory=list, description="Mention years — NOT relationship dates"
    )
    mention_year_note: Optional[str] = None
    scope_person_count: int = 0
    data_trust: Optional[Dict[str, Any]] = None


class ExtractionResetRequest(BaseModel):
    """Preview (default) or execute a cleanup of mock-derived records."""

    dry_run: bool = Field(True, description="True = preview only; nothing is written")
    run_ids: Optional[List[int]] = Field(
        None, description="Restrict the cleanup to these runs (default: all mock runs in scope)"
    )
    confirm: Optional[str] = Field(
        None, description="Must equal the group name (or document filename) to execute"
    )
    reason: Optional[str] = Field(None, max_length=500)


class ExtractionResetResponse(BaseModel):
    """Provenance-classified cleanup preview.

    Mentions, evidences and relationships are classified independently: a relationship
    that must be kept does not keep its provably-mock evidence.
    """

    dry_run: bool
    scope: Dict[str, Any]
    database_name: str
    target_runs: List[Dict[str, Any]] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    relationships_protected: List[Dict[str, Any]] = Field(default_factory=list)
    relationships_partially_cleaned: List[int] = Field(default_factory=list)
    persons_protected: List[Dict[str, Any]] = Field(default_factory=list)
    relationships_deletable: List[int] = Field(default_factory=list)
    persons_deletable: List[int] = Field(default_factory=list)
    mentions_deletable: List[int] = Field(default_factory=list)
    ambiguous_mentions: List[int] = Field(default_factory=list)
    protected_mentions: List[int] = Field(default_factory=list)
    evidence_deletable: List[int] = Field(default_factory=list)
    ambiguous_evidence: List[int] = Field(default_factory=list)
    protected_evidence: List[int] = Field(default_factory=list)
    shared_documents: List[int] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    deleted: Dict[str, int] = Field(default_factory=dict)
    reset_at: Optional[str] = None
