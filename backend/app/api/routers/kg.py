import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import logger
from app.database import get_db
from app.models.kg import Person, PersonMention, Relationship
from app.models.kg.enums import RelationType
from app.models.kg.metrics import SNAGraphMetric
from app.schemas.kg import (
    ExternalAuthorityResponse,
    PersonDetailResponse,
    PersonListResponse,
    PersonMentionListResponse,
    PersonMentionResponse,
    PersonResponse,
    RelationshipListResponse,
    RelationshipResponse,
    RelationshipReviewRequest,
    SNAGraphMetricResponse,
)
from app.schemas.kg_extraction_schema import BlockExtractionResponse
from app.schemas.kg_disambiguation import (
    AuditRecordResponse,
    ConfirmResolutionRequest,
    LinkAuthorityRequest,
    MentionDisambiguationEvaluationResponse,
    MergeEntitiesRequest,
    MergePreviewResponse,
    ReviewActionRequest,
    SplitMergeRequest,
)
from app.schemas.kg_sna import (
    SNAGraphFilterParams,
    SNANetworkSummaryResponse,
    SNAPersonMetricsResponse,
    SNAPersonNeighborsResponse,
    SNASnapshotCreateRequest,
    SNASnapshotResponse,
)
from app.services.group_scope import GroupNotFound
from app.services.kg.authority_linker import authority_linker
from app.services.kg.disambiguation_service import disambiguation_service
from app.services.kg.entity_merge_service import entity_merge_service
from app.services.kg.extraction_service import extraction_service
from app.services.kg.group_graph import (
    EVIDENCE_BUCKET_SYNTHETIC,
    EVIDENCE_BUCKET_UNKNOWN,
    EVIDENCE_BUCKET_VERIFIED,
    group_graph_state,
    load_scope,
    person_mentions_in_scope,
    referenced_person_ids,
    relationship_claim_filter,
    relationship_evidence_in_scope,
    relationship_evidence_stats,
    relationship_source_kinds,
    scope_overview,
)
from app.services.kg.sna_service import sna_service
from app.schemas.kg_group_graph import (
    ExtractionResetRequest,
    ExtractionResetResponse,
    GraphFacetsResponse,
    GroupEvidenceItem,
    GroupGraphOverviewResponse,
    GroupGraphPersonItem,
    GroupGraphRelationshipItem,
    GroupGraphScopeResponse,
    GroupPersonMentionItem,
    GroupPersonMentionsResponse,
    GroupRelationshipEvidenceResponse,
    KGExtractionRunsSummaryResponse,
    KGExtractionRunStatusResponse,
)
from app.models.kg.extraction_run import KGExtractionRun
from app.models.research_group import ResearchGroup
from app.schemas.kg_workflow import (
    BatchLinkConfirmRequest,
    BatchLinkConfirmResponse,
    BatchLinkPreviewRequest,
    BatchLinkPreviewResponse,
    GroupRelationshipReviewListResponse,
    MentionClusterListResponse,
    RelationshipReviewRequestModel,
    RelationshipReviewResponse,
    SnapshotCompareResponse,
    SnapshotCreateRequest,
    SnapshotDetail,
    SnapshotSummary,
)
from app.services.kg import (
    auto_extraction,
    extraction_reset,
    extraction_run_service,
    graph_facets,
    provenance,
    review_service,
    role_taxonomy,
    snapshot_service,
)
from app.tasks.kg_tasks import (
    extract_block_entities_task,
    extract_page_entities_task,
)

router = APIRouter(prefix="/kg", tags=["Knowledge Graph Extraction"])


def _load_group_scope_or_404(db: Session, group_id: int):
    """Resolve a group's evidence scope; unknown group is always 404 (no fallback)."""
    try:
        return load_scope(db, group_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None


@router.post(
    "/extract/block/{block_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Extract persons and relationships from a single block",
)
async def extract_block_entities(
    block_id: int,
    async_task: bool = Query(False, description="Whether to run in Celery background task"),
    overwrite: bool = Query(False, description="Whether to overwrite existing mentions in this block"),
    db: Session = Depends(get_db),
):
    """Trigger LLM entity & relation extraction for a specific text block."""
    if async_task:
        task = extract_block_entities_task.delay(block_id=block_id, overwrite_existing=overwrite)
        return {
            "status": "queued",
            "task_id": task.id,
            "block_id": block_id,
            "message": "Block extraction queued as background Celery task.",
        }

    try:
        response = await extraction_service.extract_from_block(
            db=db,
            block_id=block_id,
            overwrite_existing=overwrite,
        )
        return {
            "status": "completed",
            "data": response.model_dump(),
        }
    except Exception as exc:
        logger.error("Error in block extraction API: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/extract/page/{page_id}",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Extract all blocks on a page asynchronously",
)
def extract_page_entities(
    page_id: int,
    overwrite: bool = Query(False, description="Whether to overwrite existing mentions"),
):
    """Trigger background Celery task to extract all text blocks on a page."""
    task = extract_page_entities_task.delay(page_id=page_id, overwrite_existing=overwrite)
    return {
        "status": "queued",
        "task_id": task.id,
        "page_id": page_id,
        "message": f"Page extraction task queued for page {page_id}.",
    }


@router.get(
    "/mentions",
    response_model=PersonMentionListResponse,
    summary="Query extracted person mentions with filters",
)
def list_extracted_mentions(
    block_id: Optional[int] = Query(None, description="Filter by source block ID"),
    resolution_status: Optional[str] = Query(None, description="Filter by resolution status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Query extracted mentions with pagination and block_id grounding."""
    query = db.query(PersonMention)
    if block_id is not None:
        query = query.filter(PersonMention.block_id == block_id)
    if resolution_status:
        query = query.filter(PersonMention.resolution_status == resolution_status)

    total = query.count()
    items = query.order_by(PersonMention.id.desc()).offset(skip).limit(limit).all()

    return PersonMentionListResponse(
        items=[PersonMentionResponse.model_validate(item) for item in items],
        total=total,
    )


@router.get(
    "/relationships",
    response_model=RelationshipListResponse,
    summary="Query extracted historical relationships",
)
def list_extracted_relationships(
    relation_type: Optional[str] = Query(None, description="Filter by relation type"),
    group_id: Optional[int] = Query(None, ge=1, description="Restrict to a document group (evidence-first)"),
    view: Optional[str] = Query(
        None, pattern="^(official|candidates)$", description="official | candidates"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Query extracted relationships with pagination.

    With ``group_id`` the result is restricted to relationships that have at least one
    evidence block inside that group — an unknown group is a 404, never a global list.
    """
    query = db.query(Relationship)
    if relation_type:
        query = query.filter(Relationship.relation_type == relation_type)

    if group_id is not None:
        scope = _load_group_scope_or_404(db, group_id)
        query = query.filter(Relationship.id.in_(scope.relationship_ids))
        query = query.filter(
            relationship_claim_filter(view or "official")
        )
    elif view:
        query = query.filter(relationship_claim_filter(view))

    total = query.count()
    items = query.order_by(Relationship.id.desc()).offset(skip).limit(limit).all()

    return RelationshipListResponse(
        items=[RelationshipResponse.model_validate(item) for item in items],
        total=total,
    )


@router.post(
    "/relationships/{relationship_id}/review",
    response_model=RelationshipReviewResponse,
    summary="Record one human review decision about a claim (audited)",
)
def review_relationship(
    relationship_id: int,
    payload: RelationshipReviewRequestModel,
    db: Session = Depends(get_db),
):
    """Persist review/claim status and write an audit row.

    Provenance is deliberately *not* touched: accepting a mock-derived claim records a
    decision inside this tool, it does not turn the claim into archival evidence.
    """
    try:
        result = review_service.review_relationship(
            db,
            relationship_id,
            claim_status=payload.claim_status,
            review_status=payload.review_status,
            reason=payload.reason,
            operator=payload.reviewer or "reviewer",
            evidence_note=payload.evidence_note,
        )
    except review_service.ReviewError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "不存在" in detail
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from None
    return RelationshipReviewResponse(**result)


@router.get(
    "/persons",
    response_model=PersonListResponse,
    summary="Search and list canonical historical persons with filters",
)
def list_persons(
    q: Optional[str] = Query(None, description="Search keyword in display name, english or chinese name"),
    identity_status: Optional[str] = Query(None, description="Filter by identity confirmation status"),
    primary_role: Optional[str] = Query(None, description="Filter by historical role (e.g. security merchant, supercargo)"),
    group_id: Optional[int] = Query(None, ge=1, description="Restrict to persons supported by a document group's evidence"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List persons with flexible search and pagination.

    With ``group_id`` only persons referenced by that group's own evidence are returned
    (in-group mentions or in-group-evidenced relationships); unknown groups raise 404.
    """
    query = db.query(Person)

    if group_id is not None:
        scope = _load_group_scope_or_404(db, group_id)
        query = query.filter(Person.id.in_(referenced_person_ids(db, scope)))

    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Person.display_name.ilike(term),
                Person.canonical_name_en.ilike(term),
                Person.canonical_name_zh.ilike(term),
            )
        )

    if identity_status:
        query = query.filter(Person.identity_status == identity_status)

    if primary_role:
        query = query.filter(Person.primary_role.ilike(f"%{primary_role.strip()}%"))

    total = query.count()
    items = query.order_by(Person.id.asc()).offset(skip).limit(limit).all()

    return PersonListResponse(
        items=[PersonResponse.model_validate(item) for item in items],
        total=total,
    )


@router.get(
    "/persons/{person_id}",
    response_model=PersonDetailResponse,
    summary="Get detailed profile of a historical person including external authorities",
)
def get_person_detail(
    person_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve full biographical profile, alias metadata, and external authority links."""
    person = db.query(Person).filter(Person.id == person_id).first()
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Person {person_id} not found")

    authorities = [
        ExternalAuthorityResponse.model_validate(auth)
        for auth in getattr(person, "authorities", [])
    ]

    detail = PersonDetailResponse.model_validate(person)
    detail.external_authorities = authorities
    return detail


@router.get(
    "/stats",
    summary="Get overall KG extraction statistics",
)
def get_kg_stats(db: Session = Depends(get_db)):
    """Return counts of extracted mentions, provisional persons, and relationships."""
    mentions_count = db.query(PersonMention).count()
    persons_count = db.query(Person).count()
    relationships_count = db.query(Relationship).count()

    return {
        "total_mentions": mentions_count,
        "total_persons": persons_count,
        "total_relationships": relationships_count,
    }



# ====================================================================
# Stage 3: Disambiguation, Human Review, Authority & Entity Merging
# ====================================================================

from app.models.kg.audit import EntityOperationAudit
from app.models.kg.enums import AuthoritySource
from app.schemas.kg_disambiguation import (
    AuditRecordResponse,
    ConfirmResolutionRequest,
    LinkAuthorityRequest,
    MentionDisambiguationEvaluationResponse,
    MergeEntitiesRequest,
    MergePreviewResponse,
    ReviewActionRequest,
    SplitMergeRequest,
)
from app.services.kg import (
    authority_linker,
    disambiguation_service,
    entity_merge_service,
)


@router.get(
    "/disambiguate/mention/{mention_id}/candidates",
    response_model=MentionDisambiguationEvaluationResponse,
    summary="Evaluate candidates for a person mention with explainable multi-dimensional scores",
)
def get_mention_candidates(
    mention_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve temporally compatible candidates and multi-dimensional compatibility breakdown."""
    try:
        mention, evals = disambiguation_service.evaluate_mention_candidates(db=db, mention_id=mention_id)
        return MentionDisambiguationEvaluationResponse(
            mention_id=mention.id,
            raw_text=mention.raw_text,
            normalized_text=mention.normalized_text,
            temporal_year=mention.temporal_year,
            spatial_location=mention.spatial_location,
            current_resolution_status=mention.resolution_status.value,
            candidates_count=len(evals),
            candidates=evals,
        )
    except Exception as exc:
        logger.error("Error evaluating mention candidates: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/disambiguate/mention/{mention_id}/confirm",
    response_model=dict,
    summary="Confirm association between a mention and a canonical Person",
)
def confirm_mention_resolution(
    mention_id: int,
    payload: ConfirmResolutionRequest,
    db: Session = Depends(get_db),
):
    """Confirm resolution link and log audit entry."""
    try:
        mention = disambiguation_service.confirm_resolution(
            db=db,
            mention_id=mention_id,
            person_id=payload.person_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
        return {
            "status": "confirmed",
            "mention_id": mention.id,
            "person_id": mention.person_id,
            "resolution_status": mention.resolution_status.value,
        }
    except Exception as exc:
        logger.error("Error confirming mention resolution: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/disambiguate/mention/{mention_id}/reject",
    response_model=dict,
    summary="Reject a candidate Person for a mention",
)
def reject_mention_candidate(
    mention_id: int,
    payload: ReviewActionRequest,
    db: Session = Depends(get_db),
):
    """Reject candidate association."""
    try:
        mention = disambiguation_service.reject_candidate(
            db=db,
            mention_id=mention_id,
            person_id=payload.person_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
        return {
            "status": "rejected",
            "mention_id": mention.id,
            "resolution_status": mention.resolution_status.value,
            "remaining_candidates": mention.candidate_person_ids,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/disambiguate/mention/{mention_id}/defer",
    response_model=dict,
    summary="Defer decision on a mention due to insufficient evidence or conflicting sources",
)
def defer_mention_resolution(
    mention_id: int,
    payload: ReviewActionRequest,
    db: Session = Depends(get_db),
):
    """Defer resolution."""
    try:
        mention = disambiguation_service.defer_resolution(
            db=db,
            mention_id=mention_id,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
        return {
            "status": "deferred",
            "mention_id": mention.id,
            "resolution_status": mention.resolution_status.value,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/authority/seed-priors",
    response_model=dict,
    summary="Seed 18th-century Canton Factory historical priors and external authorities",
)
def seed_historical_priors(db: Session = Depends(get_db)):
    """Seed historical figures and authorities."""
    try:
        persons = authority_linker.seed_historical_priors(db=db)
        return {
            "status": "success",
            "seeded_persons_count": len(persons),
            "message": f"Seeded {len(persons)} historical canonical persons with authorities.",
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/entities/merge-preview",
    response_model=MergePreviewResponse,
    summary="Preview impact and detect potential conflicts before merging two persons",
)
def preview_entity_merge(
    primary_id: int = Query(..., description="ID of primary person to retain"),
    secondary_id: int = Query(..., description="ID of secondary person to merge into primary"),
    db: Session = Depends(get_db),
):
    """Preview entity merge impact."""
    try:
        report = entity_merge_service.preview_merge(
            db=db,
            primary_person_id=primary_id,
            secondary_person_id=secondary_id,
        )
        return MergePreviewResponse(**report)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/entities/merge",
    response_model=dict,
    summary="Execute auditable atomic entity merge",
)
def execute_entity_merge(
    payload: MergeEntitiesRequest,
    db: Session = Depends(get_db),
):
    """Execute merge and record snapshot."""
    try:
        audit = entity_merge_service.execute_merge(
            db=db,
            primary_person_id=payload.primary_person_id,
            secondary_person_id=payload.secondary_person_id,
            operator=payload.operator,
            reason=payload.reason,
        )
        return {
            "status": "merged",
            "audit_id": audit.id,
            "primary_person_id": audit.primary_person_id,
            "secondary_person_id": audit.secondary_person_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/entities/split",
    response_model=dict,
    summary="Reversibly restore/split previously merged entities using audit snapshot",
)
def split_entity_merge(
    payload: SplitMergeRequest,
    db: Session = Depends(get_db),
):
    """Reversibly rollback previous merge."""
    try:
        split_audit = entity_merge_service.split_or_rollback_merge(
            db=db,
            audit_id=payload.audit_id,
            operator=payload.operator,
            reason=payload.reason,
        )
        return {
            "status": "split_restored",
            "split_audit_id": split_audit.id,
            "restored_person_id": split_audit.secondary_person_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/audit-logs",
    response_model=List[AuditRecordResponse],
    summary="Query entity operations audit history",
)
def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List recent audit logs."""
    logs = (
        db.query(EntityOperationAudit)
        .order_by(EntityOperationAudit.id.desc())
        .limit(limit)
        .all()
    )
    return [
        AuditRecordResponse(
            id=log.id,
            operation_type=log.operation_type,
            primary_person_id=log.primary_person_id,
            secondary_person_id=log.secondary_person_id,
            snapshot_details=log.snapshot_details or {},
            reason=log.reason,
            operator=log.operator,
            is_reverted=log.is_reverted,
            created_at=log.created_at,
        )
        for log in logs
    ]


# =========================================================================
# Stage 4: Social Network Analysis (SNA) Endpoints
# =========================================================================

def _parse_scope_filters(
    relation_types: Optional[List[RelationType]] = Query(None, description="Filter relationship types"),
    period_start: Optional[date] = Query(None, description="Earliest date filter"),
    period_end: Optional[date] = Query(None, description="Latest date filter"),
    include_unknown_dates: bool = Query(True, description="Whether to include unknown/undated relationships"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum confidence threshold"),
    include_provisional: bool = Query(False, description="Whether to include provisional persons"),
    include_isolates: bool = Query(True, description="Whether to retain 0-degree isolated persons"),
    directed: bool = Query(True, description="Whether edges preserve direction"),
    view: Optional[str] = Query(
        None,
        pattern="^(official|candidates|all)$",
        description="official | candidates | all (all = extracted graph, provisional included)",
    ),
) -> SNAGraphFilterParams:
    """Filter dependency for group endpoints: identical to the SNA filters minus scope.

    ``group_id`` comes from the path for these routes, so declaring it as a query
    parameter would collide with the path parameter.
    """
    if period_start and period_end and period_start > period_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"period_start ({period_start}) cannot be after period_end ({period_end})",
        )
    return SNAGraphFilterParams(
        relation_types=relation_types,
        period_start=period_start,
        period_end=period_end,
        include_unknown_dates=include_unknown_dates,
        min_confidence=min_confidence,
        include_provisional=include_provisional,
        include_isolates=include_isolates,
        directed=directed,
        view=view,
    )


def _parse_sna_filters(
    relation_types: Optional[List[RelationType]] = Query(None, description="Filter relationship types"),
    period_start: Optional[date] = Query(None, description="Earliest date filter"),
    period_end: Optional[date] = Query(None, description="Latest date filter"),
    include_unknown_dates: bool = Query(True, description="Whether to include unknown/undated relationships"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum confidence threshold"),
    include_provisional: bool = Query(False, description="Whether to include provisional persons"),
    include_isolates: bool = Query(True, description="Whether to retain 0-degree isolated persons"),
    directed: bool = Query(True, description="Whether edges preserve direction"),
    group_id: Optional[int] = Query(None, ge=1, description="Restrict the network to one document group"),
    view: Optional[str] = Query(
        None,
        pattern="^(official|candidates|all)$",
        description="official | candidates | all (all = extracted graph, provisional included)",
    ),
) -> SNAGraphFilterParams:
    """Helper dependency to validate and assemble SNA filter params."""
    if period_start and period_end and period_start > period_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"period_start ({period_start}) cannot be after period_end ({period_end})",
        )
    return SNAGraphFilterParams(
        relation_types=relation_types,
        period_start=period_start,
        period_end=period_end,
        include_unknown_dates=include_unknown_dates,
        min_confidence=min_confidence,
        include_provisional=include_provisional,
        include_isolates=include_isolates,
        directed=directed,
        group_id=group_id,
        view=view,
    )


@router.get(
    "/sna/summary",
    response_model=SNANetworkSummaryResponse,
    summary="Get macro topological summary and metrics of historical Canton social network",
)
def get_network_summary(
    filters: SNAGraphFilterParams = Depends(_parse_sna_filters),
    db: Session = Depends(get_db),
):
    """Compute and return macro-level graph metrics under current filters."""
    try:
        return sna_service.get_network_summary(db=db, filters=filters)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except Exception as exc:
        logger.error("Error computing SNA network summary: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/sna/persons/{person_id}/metrics",
    response_model=SNAPersonMetricsResponse,
    summary="Get SNA centrality metrics and rankings for a specific person",
)
def get_person_sna_metrics(
    person_id: int,
    filters: SNAGraphFilterParams = Depends(_parse_sna_filters),
    db: Session = Depends(get_db),
):
    """Compute and return node-level centrality metrics and global rankings."""
    try:
        return sna_service.get_person_metrics(db=db, person_id=person_id, filters=filters)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("Error computing person SNA metrics: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/sna/persons/{person_id}/neighbors",
    response_model=SNAPersonNeighborsResponse,
    summary="Get 1-degree topological neighbors and grounded archival evidence for a person",
)
def get_person_neighbors(
    person_id: int,
    filters: SNAGraphFilterParams = Depends(_parse_sna_filters),
    db: Session = Depends(get_db),
):
    """Return ego-network neighbors and archival evidence links."""
    try:
        return sna_service.get_person_neighbors(db=db, person_id=person_id, filters=filters)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("Error querying person neighbors: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/sna/snapshot",
    response_model=SNASnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Execute SNA analysis and persist snapshot into sna_graph_metrics",
)
def create_sna_snapshot(
    payload: SNASnapshotCreateRequest,
    db: Session = Depends(get_db),
):
    """Compute SNA metrics and persist to database for historical versioning."""
    try:
        return sna_service.create_and_persist_snapshot(db=db, request=payload)
    except Exception as exc:
        logger.error("Error creating SNA snapshot: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/sna/snapshots",
    response_model=List[dict],
    summary="List historical SNA snapshots recorded in the system",
)
def list_sna_snapshots(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List historical snapshot runs."""
    try:
        return sna_service.list_snapshots(db=db, limit=limit)
    except Exception as exc:
        logger.error("Error listing SNA snapshots: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/sna/snapshots/{run_id}",
    response_model=List[SNAGraphMetricResponse],
    summary="Get all persisted person metrics for a specific SNA run_id",
)
def get_snapshot_run_metrics(
    run_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve all metrics belonging to a specific snapshot run."""
    metrics = (
        db.query(SNAGraphMetric)
        .filter(SNAGraphMetric.run_id == run_id)
        .order_by(SNAGraphMetric.degree_centrality.desc())
        .all()
    )
    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No metrics found for snapshot run_id '{run_id}'",
        )
    return metrics


# ====================================================================
# Stage 9: Group-scoped person graph (evidence-first, read-only)
# ====================================================================

@router.get(
    "/groups/{group_id}/graph/scope",
    response_model=GroupGraphScopeResponse,
    summary="Coverage and extraction state of a document group's person graph",
)
def get_group_graph_scope(group_id: int, db: Session = Depends(get_db)):
    """Report what the group actually has: documents, blocks, mentions, evidence.

    States are derived from stored data plus the persistent extraction ledger. The
    automatic-research configuration is reported separately, so "auto research is
    enabled", "a run is in progress" and "relationships were reviewed" can never be
    conflated.
    """
    scope = _load_group_scope_or_404(db, group_id)
    payload = group_graph_state(db, scope)
    group = (
        db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
    )
    payload["auto_research"] = auto_extraction.auto_research_status(group).as_dict()
    return GroupGraphScopeResponse(**payload)


@router.get(
    "/groups/{group_id}/graph/facets",
    response_model=GraphFacetsResponse,
    summary="Filter facets (relation types, relationship years) for one group",
)
def get_group_graph_facets(
    group_id: int,
    filters: SNAGraphFilterParams = Depends(_parse_scope_filters),
    db: Session = Depends(get_db),
):
    """Metadata for the filter bar, computed from the *same* subgraph as the graph.

    Counts come from :func:`group_graph.scoped_network`, so a dropdown option can never
    describe a different data set than the rendered nodes and edges. Relationship years
    come from relationship dates only; mention years are reported separately and
    labelled as mention dates.
    """
    scope = _load_group_scope_or_404(db, group_id)
    scoped = filters.model_copy(update={"group_id": group_id})
    return GraphFacetsResponse(**graph_facets.group_graph_facets(db, scope, scoped))


@router.get(
    "/groups/{group_id}/extraction/runs",
    response_model=KGExtractionRunsSummaryResponse,
    summary="Classified run history of a document group (active / paused / finished)",
)
def get_group_extraction_runs(
    group_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Report every recent run by role instead of only the newest one.

    A paused attempt and a finished run are different facts: the caller must be able to
    see both, so "the group is paused" can never hide "the group already has results".
    """
    _load_group_scope_or_404(db, group_id)
    return KGExtractionRunsSummaryResponse(
        **extraction_run_service.runs_summary(db, group_id=group_id, limit=limit)
    )


@router.post(
    "/groups/{group_id}/extraction/reset",
    response_model=ExtractionResetResponse,
    summary="Preview or execute cleanup of mock-derived extraction records",
)
def reset_group_extraction(
    group_id: int,
    payload: ExtractionResetRequest,
    db: Session = Depends(get_db),
):
    """Dry-run by default; execution needs the exact group name and a reason.

    Only records that can be attributed to a targeted (mock) run *and* pass every
    protection check are removed. Anything ambiguous is reported and left alone.
    """
    _load_group_scope_or_404(db, group_id)
    try:
        result = extraction_reset.execute_reset(
            db,
            group_id=group_id,
            run_ids=payload.run_ids,
            dry_run=payload.dry_run,
            confirm=payload.confirm,
            reason=payload.reason,
        )
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except extraction_reset.ResetRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except extraction_reset.ResetConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return ExtractionResetResponse(**result)


@router.get(
    "/groups/{group_id}/graph/overview",
    response_model=GroupGraphOverviewResponse,
    summary="Complete node/edge set of a document group's person graph",
)
def get_group_graph_overview(
    group_id: int,
    filters: SNAGraphFilterParams = Depends(_parse_scope_filters),
    db: Session = Depends(get_db),
):
    """Return every person and relationship supported by this group's evidence.

    The same filter object is used by ``/sna/summary``, so the rendered graph and the
    reported metrics describe one and the same subgraph. The response is not paginated
    on purpose: a truncated payload would contradict the metrics.
    """
    scope = _load_group_scope_or_404(db, group_id)
    # The path is the single source of truth for the scope.
    scoped_filters = filters.model_copy(update={"group_id": group_id})
    overview = scope_overview(db, scope, scoped_filters)
    evidence_counts = overview["evidence_counts"]
    evidence_stats = overview["evidence_stats"]
    bucket_totals = overview["evidence_bucket_totals"]
    return GroupGraphOverviewResponse(
        group_id=scope.group_id,
        group_name=scope.group_name,
        view=overview["view"],
        persons=[
            GroupGraphPersonItem(
                id=person.id,
                display_name=person.display_name,
                canonical_name_en=person.canonical_name_en,
                canonical_name_zh=person.canonical_name_zh,
                primary_role=person.primary_role,
                organization=person.organization,
                identity_status=str(getattr(person.identity_status, "value", person.identity_status)),
                role_source=person.role_source,
                role_category=role_taxonomy.classify_role(person.primary_role),
            )
            for person in overview["persons"]
        ],
        relationships=[
            GroupGraphRelationshipItem(
                id=relationship.id,
                source_person_id=relationship.source_person_id,
                target_person_id=relationship.target_person_id,
                relation_type=str(
                    getattr(relationship.relation_type, "value", relationship.relation_type)
                ),
                relation_label=relationship.relation_label,
                directionality=str(
                    getattr(relationship.directionality, "value", relationship.directionality)
                )
                if relationship.directionality is not None
                else None,
                claim_status=str(
                    getattr(relationship.claim_status, "value", relationship.claim_status)
                ),
                review_status=str(
                    getattr(relationship.review_status, "value", relationship.review_status)
                ),
                confidence=relationship.confidence,
                time_description=relationship.time_description,
                evidence_count=evidence_counts.get(relationship.id, 0),
                evidence_rows=int(
                    evidence_stats.get(relationship.id, {}).get("evidence_rows", 0)
                ),
                distinct_quotes=int(
                    evidence_stats.get(relationship.id, {}).get("distinct_quotes", 0)
                ),
                verified_evidence_count=int(
                    evidence_stats.get(relationship.id, {}).get(
                        EVIDENCE_BUCKET_VERIFIED, 0
                    )
                ),
                synthetic_evidence_count=int(
                    evidence_stats.get(relationship.id, {}).get(
                        EVIDENCE_BUCKET_SYNTHETIC, 0
                    )
                ),
                unknown_evidence_count=int(
                    evidence_stats.get(relationship.id, {}).get(EVIDENCE_BUCKET_UNKNOWN, 0)
                ),
                evidence_block_ids=list(scope.evidence_blocks.get(relationship.id, ())),
                source_kind=provenance.combine(
                    overview["source_kinds"].get(relationship.id, [])
                ),
                source_kind_label=provenance.describe(
                    provenance.combine(overview["source_kinds"].get(relationship.id, []))
                ),
                source_kinds=provenance.summarise(
                    overview["source_kinds"].get(relationship.id, [])
                ),
                synthetic=any(
                    provenance.is_synthetic(kind)
                    for kind in overview["source_kinds"].get(relationship.id, [])
                )
                or not overview["source_kinds"].get(relationship.id, []),
            )
            for relationship in overview["relationships"]
        ],
        scope_person_count=overview["scope_person_count"],
        scope_relationship_count=overview["scope_relationship_count"],
        evidence_rows_total=int(overview["evidence_rows_total"]),
        verified_evidence_rows=int(bucket_totals[EVIDENCE_BUCKET_VERIFIED]),
        synthetic_evidence_rows=int(bucket_totals[EVIDENCE_BUCKET_SYNTHETIC]),
        unknown_evidence_rows=int(bucket_totals[EVIDENCE_BUCKET_UNKNOWN]),
        evidence_note=(
            "evidence_count 表示证据覆盖的组内区块数（档案定位点），"
            "evidence_rows 表示已存储的证据行数；同一句关系重复出现在多个区块中仍然只是同一条主张。"
        ),
        role_counts=overview["role_counts"],
        role_known_count=overview["role_known_count"],
        role_unknown_count=overview["role_unknown_count"],
        role_source_counts=overview["role_source_counts"],
    )


@router.get(
    "/groups/{group_id}/relationships/{relationship_id}/evidence",
    response_model=GroupRelationshipEvidenceResponse,
    summary="In-group evidence rows of one relationship (kept separate per source)",
)
def get_group_relationship_evidence(
    group_id: int,
    relationship_id: int,
    limit: Optional[int] = Query(
        None, ge=1, le=1000, description="Rows to return (default 200, max 1000)"
    ),
    db: Session = Depends(get_db),
):
    """Return each in-group evidence row with its own document/page/block/quote.

    Rows outside the group are only counted, never shown as if they supported this
    group: identity sharing must not promote foreign evidence. A claim can carry one
    evidence row per block, so the list is bounded and the real totals are reported.
    """
    scope = _load_group_scope_or_404(db, group_id)
    relationship, items, out_of_scope, in_scope_total = relationship_evidence_in_scope(
        db, group_id, relationship_id, limit=limit
    )
    if relationship is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found",
        )
    # Distinct locators and provenance split, so "many rows" is never shown as "many
    # independent archival findings".
    stats = relationship_evidence_stats(db, [relationship_id], scope).get(
        relationship_id, {}
    )
    source_kinds = provenance.summarise(
        relationship_source_kinds(db, [relationship_id]).get(relationship_id, [])
    )
    return GroupRelationshipEvidenceResponse(
        group_id=group_id,
        relationship_id=relationship.id,
        source_person_id=relationship.source_person_id,
        target_person_id=relationship.target_person_id,
        relation_type=str(getattr(relationship.relation_type, "value", relationship.relation_type)),
        claim_status=str(getattr(relationship.claim_status, "value", relationship.claim_status)),
        review_status=str(getattr(relationship.review_status, "value", relationship.review_status)),
        items=[
            GroupEvidenceItem(
                **item,
                source_kind_label=provenance.describe(item.get("source_kind")),
                synthetic=provenance.is_synthetic(item.get("source_kind"))
                or item.get("source_kind") is None,
            )
            for item in items
        ],
        out_of_group_evidence_count=out_of_scope,
        in_group_evidence_count=in_scope_total,
        distinct_block_count=int(stats.get("distinct_blocks", 0)),
        distinct_quote_count=int(stats.get("distinct_quotes", 0)),
        source_kind_counts=source_kinds,
        verified_evidence_count=int(stats.get(EVIDENCE_BUCKET_VERIFIED, 0)),
        synthetic_evidence_count=int(stats.get(EVIDENCE_BUCKET_SYNTHETIC, 0)),
        unknown_evidence_count=int(stats.get(EVIDENCE_BUCKET_UNKNOWN, 0)),
        truncated=len(items) < in_scope_total,
    )


@router.get(
    "/groups/{group_id}/persons/{person_id}/mentions",
    response_model=GroupPersonMentionsResponse,
    summary="In-group mentions of one person with archival locators",
)
def get_group_person_mentions(
    group_id: int,
    person_id: int,
    limit: Optional[int] = Query(
        None, ge=1, le=1000, description="Rows to return (default 200, max 1000)"
    ),
    db: Session = Depends(get_db),
):
    """List the group's own mentions of a person (document / page / block / quote).

    Bounded like the evidence endpoint: a person can be mentioned in thousands of
    blocks, and the totals are reported instead of an unbounded payload.
    """
    _load_group_scope_or_404(db, group_id)
    person, mentions, total, unlinked_total, unlinked = person_mentions_in_scope(
        db, group_id, person_id, limit=limit
    )
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Person {person_id} not found"
        )
    return GroupPersonMentionsResponse(
        group_id=group_id,
        person_id=person.id,
        display_name=person.display_name,
        identity_status=str(getattr(person.identity_status, "value", person.identity_status)),
        items=[GroupPersonMentionItem(**mention) for mention in mentions],
        in_group_mention_count=total,
        unlinked_name_match_count=unlinked_total,
        unlinked_items=[
            GroupPersonMentionItem(**mention, linked=False) for mention in unlinked
        ],
        truncated=len(mentions) < total,
    )
# ====================================================================
# Stage 9 P1: persistent extraction runs (manual start, resumable, budgeted)
# ====================================================================

@router.post(
    "/groups/{group_id}/extraction",
    response_model=KGExtractionRunStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a knowledge-graph extraction run for a group (or one of its documents)",
)
def start_group_extraction(
    group_id: int,
    document_id: Optional[int] = Query(None, ge=1, description="Restrict the run to one document"),
    budget_blocks: Optional[int] = Query(None, ge=1, description="Stop after N blocks"),
    budget_tokens: Optional[int] = Query(None, ge=1, description="Stop after N tokens"),
    max_consecutive_failures: int = Query(3, ge=1, le=20),
    async_task: bool = Query(True, description="Dispatch to Celery (false keeps it queued)"),
    db: Session = Depends(get_db),
):
    """Create a run in the ledger; extraction is never implicit.

    This is the *manual* entry point (also the explicit historical-backfill entry for
    a group whose documents were imported before auto research existed). Extraction
    produces provisional persons and asserted relationships plus block-level
    evidence; acceptance remains a separate human action.
    """
    try:
        run = extraction_run_service.create_run(
            db,
            group_id=group_id,
            document_id=document_id,
            budget_blocks=budget_blocks,
            budget_tokens=budget_tokens,
            max_consecutive_failures=max_consecutive_failures,
            trigger="manual",
        )
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except extraction_run_service.BudgetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except extraction_run_service.RunConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None

    if async_task:
        dispatch_state, dispatch_error = auto_extraction.dispatch_run(db, run)
        if dispatch_error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "抽取任务已登记（run #%s），但队列投递失败：%s。"
                    "队列恢复后可调用 POST /api/kg/extraction/runs/%s/dispatch 重新投递。"
                    % (run.id, dispatch_error, run.id)
                ),
            )

    return KGExtractionRunStatusResponse(**extraction_run_service.run_status(db, run))


@router.get(
    "/groups/{group_id}/extraction",
    response_model=Optional[KGExtractionRunStatusResponse],
    summary="Latest extraction run of a document group (null when never started)",
)
def get_group_extraction(group_id: int, db: Session = Depends(get_db)):
    _load_group_scope_or_404(db, group_id)
    snapshot = extraction_run_service.group_extraction_snapshot(db, group_id)
    return KGExtractionRunStatusResponse(**snapshot) if snapshot else None


@router.get(
    "/extraction/runs/{run_id}",
    response_model=KGExtractionRunStatusResponse,
    summary="Progress of one extraction run",
)
def get_extraction_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="抽取任务 #%s 不存在。" % run_id
        )
    return KGExtractionRunStatusResponse(**extraction_run_service.run_status(db, run))


@router.post(
    "/extraction/runs/{run_id}/pause",
    response_model=KGExtractionRunStatusResponse,
    summary="Pause an extraction run (progress is kept)",
)
def pause_extraction_run(run_id: int, db: Session = Depends(get_db)):
    try:
        snapshot = extraction_run_service.pause_run(db, run_id)
    except extraction_run_service.RunNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except extraction_run_service.RunStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return KGExtractionRunStatusResponse(**snapshot)


@router.post(
    "/extraction/runs/{run_id}/resume",
    response_model=KGExtractionRunStatusResponse,
    summary="Resume a paused extraction run and re-dispatch it",
)
def resume_extraction_run(
    run_id: int,
    budget_blocks: Optional[int] = Query(
        None, ge=1, description="New absolute block ceiling (required after a budget pause)"
    ),
    budget_tokens: Optional[int] = Query(
        None, ge=1, description="New absolute token ceiling (required after a budget pause)"
    ),
    async_task: bool = Query(True),
    db: Session = Depends(get_db),
):
    try:
        snapshot = extraction_run_service.resume_run(
            db, run_id, budget_blocks=budget_blocks, budget_tokens=budget_tokens
        )
    except extraction_run_service.RunNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except extraction_run_service.RunStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    if async_task:
        run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one()
        state, error = auto_extraction.dispatch_run(db, run)
        snapshot = extraction_run_service.run_status(db, run)
        if error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "任务已恢复（run #%s），但队列投递失败：%s。"
                    "队列恢复后可调用 POST /api/kg/extraction/runs/%s/dispatch 重新投递。"
                    % (run_id, error, run_id)
                ),
            )
    return KGExtractionRunStatusResponse(**snapshot)


@router.post(
    "/extraction/runs/{run_id}/dispatch",
    response_model=KGExtractionRunStatusResponse,
    summary="Re-deliver a queued/running extraction run whose message was lost",
)
def redispatch_extraction_run(run_id: int, db: Session = Depends(get_db)):
    """Idempotent re-delivery.

    A run whose broker delivery failed stays in the ledger; this endpoint hands it to
    the queue again. Safe because every processed block is recorded, so a duplicate
    delivery never re-extracts or duplicates evidence.
    """
    try:
        snapshot = auto_extraction.redispatch_run(db, run_id)
    except extraction_run_service.RunNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except extraction_run_service.RunStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    if snapshot.get("dispatch_error"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="队列仍然不可用：%s" % snapshot["dispatch_error"],
        )
    return KGExtractionRunStatusResponse(**snapshot)


@router.get(
    "/extraction/dispatch-queue",
    response_model=List[KGExtractionRunStatusResponse],
    summary="Runs whose delivery to the queue failed (recoverable, never lost)",
)
def list_undelivered_runs(
    limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)
):
    runs = (
        db.query(KGExtractionRun)
        .filter(KGExtractionRun.dispatch_state == "failed")
        .order_by(KGExtractionRun.id.desc())
        .limit(limit)
        .all()
    )
    return [
        KGExtractionRunStatusResponse(**extraction_run_service.run_status(db, run))
        for run in runs
    ]


# ====================================================================
# Stage 10: review workflow (relationship review, disambiguation, snapshots)
# ====================================================================


@router.get(
    "/groups/{group_id}/relationships",
    response_model=GroupRelationshipReviewListResponse,
    summary="Relationship candidates of one group with provenance and review state",
)
def list_group_relationship_candidates(
    group_id: int,
    claim_status: Optional[str] = Query(None, description="asserted|accepted|uncertain|contested|rejected"),
    review_status: Optional[str] = Query(None, description="unreviewed|in_review|reviewed"),
    relation_type: Optional[str] = Query(None),
    source_kind: Optional[str] = Query(
        None, description="provider_reported|historical_seed|mock_text_derived|mock_constant|unknown"
    ),
    has_date: Optional[bool] = Query(None, description="Only claims with / without any date"),
    q: Optional[str] = Query(None, description="Match either endpoint name or the label"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Candidates are evidence-first: only relationships with in-group evidence appear.

    ``synthetic`` marks claims whose evidence is mock/stub derived; it is reported
    next to ``claim_status`` rather than replacing it, because a reviewed mock claim is
    still not an archival finding.
    """
    _load_group_scope_or_404(db, group_id)
    return GroupRelationshipReviewListResponse(
        **review_service.list_group_relationships(
            db,
            group_id,
            claim_status=claim_status,
            review_status=review_status,
            relation_type=relation_type,
            source_kind=source_kind,
            has_date=has_date,
            q=q,
            skip=skip,
            limit=limit,
        )
    )



@router.get(
    "/relationships/{relationship_id}/review-history",
    summary="Audit trail of review decisions for one relationship",
)
def relationship_review_history(
    relationship_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return review_service.review_history(db, relationship_id=relationship_id, limit=limit)


@router.get(
    "/groups/{group_id}/mentions/clusters",
    response_model=MentionClusterListResponse,
    summary="Unresolved mentions of one group grouped by normalized name",
)
def group_mention_clusters(
    group_id: int,
    only_unresolved: bool = Query(True),
    source_kind: Optional[str] = Query(None),
    limit_groups: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """A cluster is a candidate for review, never an identity decision."""
    _load_group_scope_or_404(db, group_id)
    return MentionClusterListResponse(
        **review_service.mention_clusters(
            db,
            group_id,
            only_unresolved=only_unresolved,
            source_kind=source_kind,
            limit_groups=limit_groups,
        )
    )


@router.post(
    "/groups/{group_id}/mentions/batch-link/preview",
    response_model=BatchLinkPreviewResponse,
    summary="Preview linking every unresolved mention of one name (writes nothing)",
)
def preview_mention_batch_link(
    group_id: int,
    payload: BatchLinkPreviewRequest,
    db: Session = Depends(get_db),
):
    _load_group_scope_or_404(db, group_id)
    try:
        return BatchLinkPreviewResponse(
            **review_service.preview_batch_link(
                db,
                group_id,
                normalized_text=payload.normalized_text,
                person_id=payload.person_id,
                new_person_name=payload.new_person_name,
            )
        )
    except review_service.ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.post(
    "/groups/{group_id}/mentions/batch-link/confirm",
    response_model=BatchLinkConfirmResponse,
    summary="Apply a previewed batch (requires the preview token and writes an audit)",
)
def confirm_mention_batch_link(
    group_id: int,
    payload: BatchLinkConfirmRequest,
    db: Session = Depends(get_db),
):
    _load_group_scope_or_404(db, group_id)
    try:
        return BatchLinkConfirmResponse(
            **review_service.confirm_batch_link(
                db,
                group_id,
                normalized_text=payload.normalized_text,
                confirm_token=payload.confirm_token,
                person_id=payload.person_id,
                new_person_name=payload.new_person_name,
                reason=payload.reason,
                operator=payload.operator or "reviewer",
            )
        )
    except review_service.ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.post(
    "/mentions/batches/{batch_id}/revert",
    summary="Revert one batch disambiguation using its recorded before-state",
)
def revert_mention_batch(
    batch_id: str,
    reason: Optional[str] = Query(None, max_length=500),
    operator: str = Query("reviewer", max_length=100),
    db: Session = Depends(get_db),
):
    try:
        return review_service.revert_batch(db, batch_id, reason=reason, operator=operator)
    except review_service.ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None


@router.post(
    "/graph/snapshots",
    response_model=SnapshotDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Store a reproducible snapshot of one group's current graph",
)
def create_graph_snapshot(payload: SnapshotCreateRequest, db: Session = Depends(get_db)):
    run_id = payload.run_id or "snap_%s_%s" % (payload.group_id, uuid.uuid4().hex[:10])
    try:
        return SnapshotDetail(
            **snapshot_service.create_snapshot(
                db,
                run_id=run_id,
                group_id=payload.group_id,
                name=payload.name,
                note=payload.note,
                view=payload.view,
                filters=payload.filters,
                created_by=payload.created_by or "reviewer",
            )
        )
    except snapshot_service.SnapshotError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None


@router.get(
    "/graph/snapshots",
    response_model=List[SnapshotSummary],
    summary="List stored graph snapshots (optionally for one group)",
)
def list_graph_snapshots(
    group_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return [SnapshotSummary(**row) for row in snapshot_service.list_snapshots(db, group_id=group_id, limit=limit)]


@router.get(
    "/graph/snapshots/compare",
    response_model=SnapshotCompareResponse,
    summary="Compare two snapshots (structure, review, evidence and provenance apart)",
)
def compare_graph_snapshots(
    left: str = Query(..., description="run_id of the earlier snapshot"),
    right: str = Query(..., description="run_id of the later snapshot"),
    db: Session = Depends(get_db),
):
    try:
        return SnapshotCompareResponse(**snapshot_service.compare_snapshots(db, left, right))
    except snapshot_service.SnapshotError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None


@router.get(
    "/graph/snapshots/{run_id}",
    response_model=SnapshotDetail,
    summary="Stored snapshot detail (nodes, edges, filters, provenance)",
)
def get_graph_snapshot(run_id: str, db: Session = Depends(get_db)):
    try:
        return SnapshotDetail(**snapshot_service.get_snapshot(db, run_id))
    except snapshot_service.SnapshotError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
