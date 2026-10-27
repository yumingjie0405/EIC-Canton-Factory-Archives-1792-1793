"""Group-scoped person graph: evidence-first resolution.

A document group is the research boundary. A person or relationship may appear in
a group's graph **only when that group's own documents supply the evidence**:

    research_group_documents.document_id
      -> pages.document_id
      -> blocks.id                      (the group's evidence domain)
      -> person_mentions.block_id       (persons seen in the group)
      -> relationship_evidences.block_id (relationships evidenced in the group)

Global identity is shared (one ``persons`` row may appear in many groups) and the
review status of a relationship is global, but *membership in a group graph is
derived from evidence only*. A relationship whose two endpoints happen to be in
the group while every piece of evidence lives in another group is therefore
excluded — identity sharing never turns out-of-group evidence into in-group
evidence.

This module is the single place that answers "which evidence belongs to group X".
Both the SNA engine and the read-only graph endpoints resolve the scope through
:func:`load_scope`, so the numbers and the rendered graph cannot diverge.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.block import Block
from app.models.document import Document
from app.models.kg.enums import ClaimStatus, EvidenceRole, IdentityStatus, ResolutionStatus, RoleSource
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.models.research_group import ResearchGroup, ResearchGroupDocument
from app.services.group_scope import GroupNotFound
from app.services.kg import mention_linker, provenance, role_taxonomy

#: Extraction state derived from stored data (there is no persistent extraction-run
#: ledger yet, so "running / paused / failed" cannot be reported honestly).
STATE_NO_DOCUMENTS = "no_documents"
STATE_NO_TEXT = "no_text"
STATE_NOT_EXTRACTED = "not_extracted"
STATE_PARTIAL = "partial_extraction"
STATE_EXTRACTED_EMPTY = "extracted_empty"
STATE_READY = "ready"
#: States derived from the persistent extraction ledger (0005).
STATE_QUEUED_EXTRACTION = "queued_extraction"
STATE_EXTRACTING = "extracting"
STATE_PAUSED_EXTRACTION = "paused_extraction"
STATE_EXTRACTION_FAILED = "extraction_failed"

_LEDGER_STATE_MAP = {
    "queued": STATE_QUEUED_EXTRACTION,
    "running": STATE_EXTRACTING,
    "paused_budget": STATE_PAUSED_EXTRACTION,
    "paused_error": STATE_PAUSED_EXTRACTION,
    "paused_format": STATE_PAUSED_EXTRACTION,
    "paused_manual": STATE_PAUSED_EXTRACTION,
    "failed": STATE_EXTRACTION_FAILED,
}

#: Relationship claim statuses considered "official" (usable as established fact).
OFFICIAL_CLAIM_STATUSES = (ClaimStatus.ACCEPTED,)
#: Claim statuses shown only in the explicit candidate view.
CANDIDATE_CLAIM_STATUSES = (
    ClaimStatus.ASSERTED,
    ClaimStatus.UNCERTAIN,
    ClaimStatus.CONTESTED,
)


@dataclass(frozen=True)
class GroupGraphScope:
    """The evidence domain of one document group."""

    group_id: int
    group_name: str
    document_ids: Tuple[int, ...]
    block_ids: frozenset
    mentioned_person_ids: frozenset
    person_ids: frozenset
    relationship_ids: frozenset
    evidence_blocks: Dict[int, Tuple[int, ...]]

    @property
    def document_count(self) -> int:
        return len(self.document_ids)

    @property
    def block_count(self) -> int:
        return len(self.block_ids)


def _ids(query) -> List[int]:
    return [row[0] for row in query.all()]


def load_scope(db: Session, group_id: int) -> GroupGraphScope:
    """Resolve the group's evidence domain. Raises :class:`GroupNotFound`."""
    group = db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
    if group is None:
        raise GroupNotFound("文档组 #%s 不存在或无权访问。" % group_id)

    document_ids = tuple(
        _ids(
            db.query(ResearchGroupDocument.document_id).filter(
                ResearchGroupDocument.group_id == group.id
            )
        )
    )

    block_ids: frozenset = frozenset()
    if document_ids:
        block_ids = frozenset(
            _ids(
                db.query(Block.id)
                .join(Page, Page.id == Block.page_id)
                .filter(Page.document_id.in_(document_ids))
            )
        )

    mentioned_person_ids: frozenset = frozenset()
    evidence_blocks: Dict[int, Tuple[int, ...]] = {}
    relationship_ids: frozenset = frozenset()
    if block_ids:
        mentioned_person_ids = frozenset(
            _ids(
                db.query(PersonMention.person_id)
                .filter(
                    PersonMention.block_id.in_(block_ids),
                    PersonMention.person_id.isnot(None),
                    PersonMention.resolution_status != ResolutionStatus.REJECTED,
                )
                .distinct()
            )
        )

        rows = (
            db.query(RelationshipEvidence.relationship_id, RelationshipEvidence.block_id)
            .filter(RelationshipEvidence.block_id.in_(block_ids))
            .all()
        )
        collected: Dict[int, List[int]] = {}
        for relationship_id, block_id in rows:
            collected.setdefault(relationship_id, []).append(block_id)
        evidence_blocks = {
            relationship_id: tuple(sorted(set(blocks)))
            for relationship_id, blocks in collected.items()
        }
        relationship_ids = frozenset(evidence_blocks.keys())

    endpoint_person_ids: frozenset = frozenset()
    if relationship_ids:
        endpoints = (
            db.query(Relationship.source_person_id, Relationship.target_person_id)
            .filter(Relationship.id.in_(relationship_ids))
            .all()
        )
        endpoint_person_ids = frozenset(
            {person for pair in endpoints for person in pair if person is not None}
        )

    return GroupGraphScope(
        group_id=group.id,
        group_name=group.name,
        document_ids=document_ids,
        block_ids=block_ids,
        mentioned_person_ids=mentioned_person_ids,
        person_ids=mentioned_person_ids | endpoint_person_ids,
        relationship_ids=relationship_ids,
        evidence_blocks=evidence_blocks,
    )


def _identity_filtered_ids(db: Session, scope: GroupGraphScope, include_provisional: bool) -> frozenset:
    """Scope persons that pass the identity rules (REJECTED never, provisional optional)."""
    if not scope.person_ids:
        return frozenset()
    query = db.query(Person.id).filter(Person.id.in_(scope.person_ids))
    query = query.filter(Person.identity_status != IdentityStatus.REJECTED)
    if not include_provisional:
        query = query.filter(Person.identity_status == IdentityStatus.ACCEPTED)
    return frozenset(_ids(query))


def _matches_relationship_filters(relationship: Relationship, filters) -> bool:
    """Apply the SNA filters that are cheaper in Python for a scoped edge set."""
    if filters is None:
        return True
    if filters.relation_types:
        wanted = {
            str(getattr(value, "value", value)) for value in filters.relation_types
        }
        actual = str(getattr(relationship.relation_type, "value", relationship.relation_type))
        if actual not in wanted:
            return False
    if filters.min_confidence is not None:
        if relationship.confidence is None or relationship.confidence < filters.min_confidence:
            return False
    if relationship.valid_from is None and relationship.valid_to is None:
        return bool(filters.include_unknown_dates)
    if filters.period_start is not None and relationship.valid_to is not None:
        if relationship.valid_to < filters.period_start:
            return False
    if filters.period_end is not None and relationship.valid_from is not None:
        if relationship.valid_from > filters.period_end:
            return False
    return True


def scoped_network(db: Session, scope: GroupGraphScope, filters) -> Tuple[frozenset, List[Relationship]]:
    """The one authoritative definition of a group's subgraph.

    Returns the person ids and the relationships that may be rendered *and* measured.
    Both the SNA service and the read-only overview endpoint call this, so a displayed
    edge can never be missing from the metrics (or vice versa).
    """
    view = filters.resolved_view() if filters is not None else "all"
    if view == "all":
        # "All extracted" is the default group view: newly extracted persons are
        # provisional by definition, so they must be visible together with their
        # sources. Acceptance is still a separate, human decision.
        include_provisional = True
    else:
        include_provisional = bool(filters.include_provisional) if filters is not None else False

    identity_ids = _identity_filtered_ids(db, scope, include_provisional)
    if not identity_ids or not scope.relationship_ids:
        relationships: List[Relationship] = []
    else:
        relationships = (
            db.query(Relationship)
            .filter(
                Relationship.id.in_(scope.relationship_ids),
                Relationship.source_person_id.in_(identity_ids),
                Relationship.target_person_id.in_(identity_ids),
                Relationship.source_person_id != Relationship.target_person_id,
                relationship_claim_filter(view),
            )
            .order_by(Relationship.id.asc())
            .all()
        )
        relationships = [
            relationship
            for relationship in relationships
            if _matches_relationship_filters(relationship, filters)
        ]

    endpoint_ids = {
        person_id
        for relationship in relationships
        for person_id in (relationship.source_person_id, relationship.target_person_id)
    }
    mentioned_ids = scope.mentioned_person_ids & identity_ids
    person_ids = endpoint_ids | mentioned_ids
    if filters is not None and not filters.include_isolates:
        person_ids = endpoint_ids
    return frozenset(person_ids), relationships


def allowed_person_ids(db: Session, scope: GroupGraphScope, include_provisional: bool) -> frozenset:
    """Persons visible in the group graph (identity rules applied on top of scope).

    Persons without any in-group mention and without an in-group-evidenced
    relationship are never returned, so a global identity cannot leak into a group
    just because it exists in another group.
    """
    return _identity_filtered_ids(db, scope, include_provisional)


def relationship_claim_filter(view: Optional[str]):
    """Claim-status predicate for the requested view (``official`` / ``candidates``)."""
    if view == "candidates":
        return Relationship.claim_status.in_(CANDIDATE_CLAIM_STATUSES)
    if view == "official":
        return Relationship.claim_status.in_(OFFICIAL_CLAIM_STATUSES)
    return Relationship.claim_status != ClaimStatus.REJECTED


def group_graph_state(db: Session, scope: GroupGraphScope) -> Dict[str, object]:
    """Data-derived extraction/coverage state for the group (no invented progress)."""
    from app.services.kg.extraction_run_service import runs_summary
    from app.services.kg.graph_facets import data_trust

    summary = runs_summary(db, group_id=scope.group_id)
    active_run = summary["active_run"]
    paused_run = summary["paused_run"]
    finished_run = summary["last_finished_run"]
    blocks_with_mentions = 0
    mention_count = 0
    pending_mentions = 0
    if scope.block_ids:
        mention_rows = (
            db.query(PersonMention.block_id, PersonMention.review_status)
            .filter(
                PersonMention.block_id.in_(scope.block_ids),
                PersonMention.resolution_status != ResolutionStatus.REJECTED,
            )
            .all()
        )
        mention_count = len(mention_rows)
        blocks_with_mentions = len({block_id for block_id, _ in mention_rows})
        pending_mentions = sum(1 for _, review in mention_rows if str(getattr(review, "value", review)) != "reviewed")

    # Extraction also produces relationship evidence, which may exist without a
    # person_mentions row (e.g. relations written by an earlier pipeline). Both count
    # as "this block has been processed".
    evidence_block_ids = {
        block_id for blocks in scope.evidence_blocks.values() for block_id in blocks
    }
    blocks_with_extraction = len(
        {block_id for block_id, _ in mention_rows} | evidence_block_ids
    ) if scope.block_ids else 0

    official = 0
    candidates = 0
    if scope.relationship_ids:
        rows = (
            db.query(Relationship.claim_status, Relationship.review_status)
            .filter(Relationship.id.in_(scope.relationship_ids))
            .all()
        )
        for claim, _review in rows:
            value = str(getattr(claim, "value", claim))
            if value == ClaimStatus.ACCEPTED.value:
                official += 1
            elif value in {status.value for status in CANDIDATE_CLAIM_STATUSES}:
                candidates += 1

    if not scope.document_ids:
        state = STATE_NO_DOCUMENTS
        message = "该文档组内还没有文档，无法建立人物图谱。"
    elif not scope.block_ids:
        state = STATE_NO_TEXT
        message = "组内文档尚无文本区块（OCR/导入未完成），无法建立人物图谱。"
    elif active_run is not None:
        # Live work always wins: it describes what is happening right now.
        state = _LEDGER_STATE_MAP.get(active_run["status"], STATE_EXTRACTING)
        if state == STATE_QUEUED_EXTRACTION:
            message = "抽取任务 #%s 已排队（共 %s 个区块），等待处理。" % (
                active_run["run_id"],
                active_run["total_blocks"],
            )
        else:
            message = "正在抽取（任务 #%s）：已处理 %s / %s 个区块。" % (
                active_run["run_id"],
                active_run["attempted_blocks"],
                active_run["total_blocks"],
            )
        if scope.person_ids or scope.relationship_ids:
            message += "已抽取部分（%d 位人物 / %d 条关系）已在下方图谱中展示。" % (
                len(scope.person_ids),
                len(scope.relationship_ids),
            )
    elif paused_run is not None and finished_run is None:
        # Nothing ever finished: the paused attempt *is* the current state.
        state = STATE_PAUSED_EXTRACTION
        message = "抽取任务 #%s 已暂停：%s" % (
            paused_run["run_id"],
            paused_run["pause_reason"] or "需要人工确认后恢复。",
        )
    elif blocks_with_extraction == 0:
        state = STATE_NOT_EXTRACTED
        message = (
            "该文档组尚未进行知识图谱抽取：%d 个文本区块中没有人物提及，也没有关系证据。"
            "可以在本页启动抽取（逐篇文档，可暂停与恢复）。"
            % scope.block_count
        )
        if paused_run is not None:
            message += "（任务 #%s 处于暂停状态。）" % paused_run["run_id"]
    elif not scope.person_ids and not scope.relationship_ids:
        state = STATE_EXTRACTED_EMPTY
        message = "抽取已在该组文本上执行，但按当前纳入规则没有识别出有效人物或关系。"
    elif blocks_with_extraction < scope.block_count:
        state = STATE_PARTIAL
        message = "该文档组只有部分文本区块完成抽取（%d / %d），图谱不完整。" % (
            blocks_with_extraction,
            scope.block_count,
        )
    else:
        state = STATE_READY
        message = "该文档组的图谱基于组内证据构建。"
        if finished_run is not None:
            message += "最近完成的任务为 #%s（%s）。" % (
                finished_run["run_id"],
                finished_run["status"],
            )

    # A paused attempt never hides a finished one: say both, so "paused" is not read
    # as "the group has no results".
    if paused_run is not None and finished_run is not None and active_run is None:
        message += " 另有任务 #%s 处于暂停状态（%s），不影响已完成的结果。" % (
            paused_run["run_id"],
            paused_run["pause_reason"] or "人工暂停",
        )

    return {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "document_count": scope.document_count,
        "block_count": scope.block_count,
        "blocks_with_mentions": blocks_with_mentions,
        "blocks_with_extraction": blocks_with_extraction,
        "mention_count": mention_count,
        "pending_mention_reviews": pending_mentions,
        "person_count": len(scope.person_ids),
        "relationship_count_official": official,
        "relationship_count_candidates": candidates,
        "extraction_state": state,
        "state_message": message,
        "task_tracking_available": True,
        "extraction_run": summary["primary_run"],
        "extraction_runs": summary,
        "data_trust": data_trust(db, group_id=scope.group_id),
    }


def person_role_summary(persons) -> Dict[str, object]:
    """Role categories and provenance for a set of persons.

    Counted from the *actual* person rows in the current subgraph, so the legend cannot
    advertise a category that is not on screen. ``unknown`` is reported separately from
    "other": the former means "no role was ever recorded".
    """
    counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    for person in persons:
        category = role_taxonomy.classify_role(person.primary_role)
        counts[category] = counts.get(category, 0) + 1
        source = person.role_source or (
            RoleSource.UNKNOWN.value if (person.primary_role or "").strip() else None
        )
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
    known = sum(value for key, value in counts.items() if key != role_taxonomy.ROLE_CATEGORY_UNKNOWN)
    return {
        "role_counts": counts,
        "role_known_count": known,
        "role_unknown_count": counts.get(role_taxonomy.ROLE_CATEGORY_UNKNOWN, 0),
        "role_source_counts": source_counts,
    }


def scope_overview(
    db: Session,
    scope: GroupGraphScope,
    filters=None,
) -> Dict[str, object]:
    """Complete node/edge set of the group subgraph (never truncated).

    Uses :func:`scoped_network`, the same definition the SNA engine measures, so the
    rendered graph and the reported metrics describe the same network.
    """
    person_ids, relationships = scoped_network(db, scope, filters)
    view = filters.resolved_view() if filters is not None else "official"

    persons = (
        db.query(Person).filter(Person.id.in_(person_ids)).order_by(Person.id.asc()).all()
        if person_ids
        else []
    )

    evidence_counts: Dict[int, int] = {
        relationship.id: len(scope.evidence_blocks.get(relationship.id, ()))
        for relationship in relationships
    }

    relationship_ids = [relationship.id for relationship in relationships]
    source_kinds = relationship_source_kinds(db, relationship_ids)
    evidence_stats = relationship_evidence_stats(db, relationship_ids, scope)

    totals = {EVIDENCE_BUCKET_VERIFIED: 0, EVIDENCE_BUCKET_SYNTHETIC: 0, EVIDENCE_BUCKET_UNKNOWN: 0}
    evidence_rows_total = 0
    for entry in evidence_stats.values():
        evidence_rows_total += int(entry["evidence_rows"])
        for bucket in totals:
            totals[bucket] += int(entry[bucket])

    return {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "view": view or "official",
        "persons": persons,
        "relationships": relationships,
        "evidence_counts": evidence_counts,
        "evidence_stats": evidence_stats,
        "evidence_rows_total": evidence_rows_total,
        "evidence_bucket_totals": totals,
        "source_kinds": source_kinds,
        "scope_person_count": len(scope.person_ids),
        "scope_relationship_count": len(scope.relationship_ids),
        # Role coverage of exactly the persons rendered in this subgraph.
        **person_role_summary(persons),
    }


def relationship_source_kinds(
    db: Session, relationship_ids: Sequence[int]
) -> Dict[int, List[str]]:
    """Raw provenance kinds of each claim's evidence rows (empty list = no evidence)."""
    if not relationship_ids:
        return {}
    rows = (
        db.query(RelationshipEvidence.relationship_id, RelationshipEvidence.source_kind)
        .filter(RelationshipEvidence.relationship_id.in_(list(relationship_ids)))
        .all()
    )
    collected: Dict[int, List[str]] = {}
    for relationship_id, kind in rows:
        collected.setdefault(relationship_id, []).append(kind)
    return collected


#: Provenance buckets used for the evidence counters. A relationship can carry one
#: evidence row *per block*, so a single claim often has thousands of rows: the API must
#: report how many *independent locators* support it, not the row count.
EVIDENCE_BUCKET_VERIFIED = "verified"
EVIDENCE_BUCKET_SYNTHETIC = "synthetic"
EVIDENCE_BUCKET_UNKNOWN = "unknown"


def evidence_bucket(source_kind: Optional[str]) -> str:
    """Classify one evidence row into a trust bucket (see :mod:`app.services.kg.provenance`)."""
    if provenance.is_synthetic(source_kind):
        return EVIDENCE_BUCKET_SYNTHETIC
    if provenance.is_unverified(source_kind):
        return EVIDENCE_BUCKET_UNKNOWN
    return EVIDENCE_BUCKET_VERIFIED


def relationship_evidence_stats(
    db: Session,
    relationship_ids: Sequence[int],
    scope: Optional[GroupGraphScope] = None,
) -> Dict[int, Dict[str, object]]:
    """Per-relationship evidence accounting, restricted to the group when scoped.

    ``evidence_count`` in the overview used to be read by the UI as "how many archival
    facts support this claim", which was misleading: a claim extracted from 6 000 blocks
    is still one claim. This returns the three distinct quantities separately:

    * ``evidence_rows`` — stored evidence rows (the raw count);
    * ``distinct_blocks`` / ``distinct_quotes`` — independent archival locators;
    * ``verified``/``synthetic``/``unknown`` — the same rows split by provenance.

    Counting happens in SQL so a claim with thousands of rows costs one aggregate query.
    """
    ids = [int(relationship_id) for relationship_id in relationship_ids]
    if not ids:
        return {}

    in_scope = None
    if scope is not None:
        in_scope = (
            RelationshipEvidence.block_id.in_(scope.block_ids) if scope.block_ids else None
        )
        if in_scope is None:
            # A group without blocks supports no evidence at all.
            return {}

    aggregate = db.query(
        RelationshipEvidence.relationship_id.label("relationship_id"),
        func.count(RelationshipEvidence.id).label("evidence_rows"),
        func.count(func.distinct(RelationshipEvidence.block_id)).label("distinct_blocks"),
        func.count(func.distinct(RelationshipEvidence.quote_text)).label("distinct_quotes"),
    )
    if in_scope is not None:
        aggregate = aggregate.filter(in_scope)
    rows = aggregate.filter(RelationshipEvidence.relationship_id.in_(ids)).group_by(
        RelationshipEvidence.relationship_id
    )

    stats: Dict[int, Dict[str, object]] = {}
    for relationship_id, evidence_rows, distinct_blocks, distinct_quotes in rows.all():
        stats[int(relationship_id)] = {
            "evidence_rows": int(evidence_rows or 0),
            "distinct_blocks": int(distinct_blocks or 0),
            "distinct_quotes": int(distinct_quotes or 0),
            EVIDENCE_BUCKET_VERIFIED: 0,
            EVIDENCE_BUCKET_SYNTHETIC: 0,
            EVIDENCE_BUCKET_UNKNOWN: 0,
        }

    kind_query = db.query(
        RelationshipEvidence.relationship_id,
        RelationshipEvidence.source_kind,
        func.count(RelationshipEvidence.id),
    )
    if in_scope is not None:
        kind_query = kind_query.filter(in_scope)
    for relationship_id, source_kind, count in kind_query.filter(
        RelationshipEvidence.relationship_id.in_(ids)
    ).group_by(RelationshipEvidence.relationship_id, RelationshipEvidence.source_kind):
        bucket = evidence_bucket(source_kind)
        entry = stats.setdefault(
            int(relationship_id),
            {
                "evidence_rows": 0,
                "distinct_blocks": 0,
                "distinct_quotes": 0,
                EVIDENCE_BUCKET_VERIFIED: 0,
                EVIDENCE_BUCKET_SYNTHETIC: 0,
                EVIDENCE_BUCKET_UNKNOWN: 0,
            },
        )
        entry[bucket] = int(entry[bucket]) + int(count or 0)

    return stats


def referenced_person_ids(db: Session, scope: GroupGraphScope) -> frozenset:
    """Persons referenced by the group's evidence, ignoring identity filtering.

    Used by the person *list* endpoint (which has its own identity filter): a person
    counts when the group mentions them, or when they are an endpoint of an in-scope
    relationship that has not been rejected. Rejected claims do not create persons.
    """
    endpoints = set()
    if scope.relationship_ids:
        rows = (
            db.query(Relationship.source_person_id, Relationship.target_person_id)
            .filter(
                Relationship.id.in_(scope.relationship_ids),
                Relationship.claim_status != ClaimStatus.REJECTED,
            )
            .all()
        )
        endpoints = {person for pair in rows for person in pair if person is not None}
    return frozenset(scope.mentioned_person_ids | endpoints)


#: Default/max number of evidence or mention rows returned by the tracing endpoints.
#: A claim can legitimately carry thousands of rows (one per block); returning all of
#: them with the joined document/page columns exhausts the driver's memory, so the
#: payload is bounded and the true totals are reported instead.
TRACE_ROW_LIMIT = 200
TRACE_ROW_LIMIT_MAX = 1000


def clamp_trace_limit(limit: Optional[int]) -> int:
    if limit is None:
        return TRACE_ROW_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return TRACE_ROW_LIMIT
    if value <= 0:
        return TRACE_ROW_LIMIT
    return min(value, TRACE_ROW_LIMIT_MAX)


def relationship_evidence_in_scope(
    db: Session,
    group_id: int,
    relationship_id: int,
    limit: Optional[int] = None,
) -> Tuple[Optional[Relationship], List[Dict[str, object]], int, int]:
    """Evidence rows of one relationship, split by "in this group" vs elsewhere.

    Multiple evidence entries are returned separately (document / page / block /
    quote per row); they are never merged into one unattributable quotation.

    Returns ``(relationship, items, out_of_scope, total)``. Only ``limit`` rows are
    materialised (default 200): the query selects the needed columns instead of whole
    ORM entities, because the joins would otherwise drag ``documents.full_markdown``
    and ``pages.markdown_content`` along for every evidence row.
    """
    scope = load_scope(db, group_id)
    relationship = db.query(Relationship).filter(Relationship.id == relationship_id).one_or_none()
    if relationship is None:
        return None, [], 0, 0

    in_group = RelationshipEvidence.block_id.in_(scope.block_ids) if scope.block_ids else None

    total = (
        db.query(func.count(RelationshipEvidence.id))
        .filter(RelationshipEvidence.relationship_id == relationship_id)
        .scalar()
        or 0
    )
    if in_group is not None:
        in_scope_total = (
            db.query(func.count(RelationshipEvidence.id))
            .filter(RelationshipEvidence.relationship_id == relationship_id, in_group)
            .scalar()
            or 0
        )
    else:
        in_scope_total = 0
    out_of_scope = max(total - in_scope_total, 0)

    query = (
        db.query(
            RelationshipEvidence.id,
            RelationshipEvidence.evidence_role,
            RelationshipEvidence.quote_text,
            RelationshipEvidence.source_kind,
            RelationshipEvidence.extraction_run_id,
            Block.id,
            Block.block_index,
            Block.block_label,
            Block.ocr_text_corrected,
            Block.ocr_text_raw,
            Page.page_number,
            Document.id,
            Document.original_filename,
            Document.filename,
        )
        .join(Block, Block.id == RelationshipEvidence.block_id)
        .join(Page, Page.id == Block.page_id)
        .join(Document, Document.id == Page.document_id)
        .filter(RelationshipEvidence.relationship_id == relationship_id)
    )
    if in_group is not None:
        query = query.filter(in_group)
    rows = query.order_by(RelationshipEvidence.id.asc()).limit(clamp_trace_limit(limit)).all()

    in_scope: List[Dict[str, object]] = [
        {
            "evidence_id": evidence_id,
            "document_id": document_id,
            "document_name": document_name or document_filename or "",
            "page_number": page_number,
            "block_id": block_id,
            "block_index": block_index,
            "block_label": block_label,
            "quote_text": quote_text,
            "block_text": ocr_text_corrected or ocr_text_raw,
            "evidence_role": str(getattr(evidence_role, "value", evidence_role)),
            "source_kind": source_kind,
            "extraction_run_id": extraction_run_id,
        }
        for (
            evidence_id,
            evidence_role,
            quote_text,
            source_kind,
            extraction_run_id,
            block_id,
            block_index,
            block_label,
            ocr_text_corrected,
            ocr_text_raw,
            page_number,
            document_id,
            document_name,
            document_filename,
        ) in rows
    ]
    return relationship, in_scope, out_of_scope, in_scope_total


def _mention_locator_query(db: Session):
    """Column-level mention query with archival locators (never whole ORM entities).

    Shared by the linked and the same-name-unlinked reads so both produce the same row
    shape; loading ``Page``/``Document`` entities here is what caused the earlier
    ``MemoryError`` on high-volume claims.
    """
    return (
        db.query(
            PersonMention.id,
            PersonMention.raw_text,
            PersonMention.normalized_text,
            PersonMention.mention_type,
            PersonMention.resolution_status,
            PersonMention.review_status,
            PersonMention.temporal_year,
            PersonMention.evidence_text,
            Block.id,
            Block.block_index,
            Block.ocr_text_corrected,
            Block.ocr_text_raw,
            Page.page_number,
            Document.id,
            Document.original_filename,
            Document.filename,
        )
        .join(Block, Block.id == PersonMention.block_id)
        .join(Page, Page.id == Block.page_id)
        .join(Document, Document.id == Page.document_id)
    )


def _mention_row_to_item(row) -> Dict[str, object]:
    (
        mention_id,
        raw_text,
        normalized_text,
        mention_type,
        resolution_status,
        review_status,
        temporal_year,
        evidence_text,
        block_id,
        block_index,
        ocr_text_corrected,
        ocr_text_raw,
        page_number,
        document_id,
        document_name,
        document_filename,
    ) = row
    return {
        "mention_id": mention_id,
        "raw_text": raw_text,
        "normalized_text": normalized_text,
        "mention_type": str(getattr(mention_type, "value", mention_type)),
        "resolution_status": str(getattr(resolution_status, "value", resolution_status)),
        "review_status": str(getattr(review_status, "value", review_status)),
        "temporal_year": temporal_year,
        "document_id": document_id,
        "document_name": document_name or document_filename or "",
        "page_number": page_number,
        "block_id": block_id,
        "block_index": block_index,
        "evidence_text": evidence_text or ocr_text_corrected or ocr_text_raw,
    }


def person_mentions_in_scope(
    db: Session,
    group_id: int,
    person_id: int,
    limit: Optional[int] = None,
    include_unlinked_matches: bool = True,
) -> Tuple[Optional[Person], List[Dict[str, object]], int, int, List[Dict[str, object]]]:
    """Mentions of one person inside the group, with document/page/block locators.

    Returns ``(person, items, total_in_scope, unlinked_total, unlinked_items)``. The
    fourth and fifth values describe mentions whose *name* matches this person but whose
    ``person_id`` is still NULL: the drawer used to report "no mentions" for people the
    archive clearly names, which read as "this person never appears in the sources".
    Only ``limit`` rows are materialised for the same reason as
    :func:`relationship_evidence_in_scope`.
    """
    scope = load_scope(db, group_id)
    person = db.query(Person).filter(Person.id == person_id).one_or_none()
    if person is None:
        return None, [], 0, 0, []

    if not scope.block_ids:
        return person, [], 0, 0, []

    in_group = PersonMention.block_id.in_(scope.block_ids)
    not_rejected = PersonMention.resolution_status != ResolutionStatus.REJECTED
    total = (
        db.query(func.count(PersonMention.id))
        .filter(PersonMention.person_id == person_id, in_group, not_rejected)
        .scalar()
        or 0
    )

    rows = (
        _mention_locator_query(db)
        .filter(PersonMention.person_id == person_id, in_group, not_rejected)
        .order_by(PersonMention.id.asc())
        .limit(clamp_trace_limit(limit))
        .all()
    )

    mentions: List[Dict[str, object]] = [_mention_row_to_item(row) for row in rows]

    unlinked_total = 0
    unlinked_mentions: List[Dict[str, object]] = []
    if include_unlinked_matches:
        unlinked_total, unlinked_rows = mention_linker.unlinked_name_matches(
            db,
            person,
            list(scope.block_ids),
            clamp_trace_limit(limit),
            query=_mention_locator_query(db).filter(PersonMention.person_id.is_(None), not_rejected),
        )
        unlinked_mentions = [_mention_row_to_item(row) for row in unlinked_rows]

    return person, mentions, total, unlinked_total, unlinked_mentions


def evidence_roles() -> Sequence[str]:
    """Controlled vocabulary for evidence roles (for UI labels)."""
    return tuple(role.value for role in EvidenceRole)
