"""Audited cleanup of derived knowledge-graph records.

Why this is not a "delete everything from the group" button
----------------------------------------------------------
A mention or an evidence row does **not** carry the id of the run that produced it.
Provenance can only be reconstructed indirectly:

    kg_extraction_items(run_id, block_id, status='succeeded')
      + the run's execution window
      + the record's own (block_id, created_at)

That is enough for an honest preview, but it is *not* proof that a record belongs to the
targeted run. Every candidate is therefore classified as

* **provable** — its block was processed successfully by a targeted run and the record
  was written inside that run's window;
* **ambiguous** — its block was touched by a targeted run, but the timestamp falls
  outside the window, or the block was also processed by a run that is *not* targeted
  (so a genuine record could be mixed in);
* **protected** — records a saved research snapshot references *by id*, accepted
  claims, and persons that are still referenced anywhere.

Only provable records are eligible for deletion, and only when no protection applies.
Ambiguous records are reported with a count and never touched.

Referencing a block is **not** a dependency on the derived rows written onto it: a
research source stores ``block_id`` plus its own copy of the quote, so the archival
block (never touched here) is what must survive. Such blocks are reported, and the
provable mock rows on them are still classified normally.

Evidence and relationship protection are decided separately: a claim that must be kept
(accepted, or still supported by evidence of unknown provenance) keeps its *claim*, but
the evidence that provably belongs to the mock run may still be removable. Those
relationships are listed under ``relationships_partially_cleaned``.

The run rows are history: they are never deleted. A real execution stamps
``reset_at`` / ``reset_reason`` on them and writes one ``kg_extraction_reset_audit``
row. A dry-run writes nothing at all (it is a pure read-only preview).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import logger
from app.database_guard import FORBIDDEN_DATABASES
from app.models.block import Block
from app.models.document import Document
from app.models.kg.audit import EntityOperationAudit
from app.models.kg.authority import ExternalAuthority
from app.models.kg.enums import ClaimStatus, IdentityStatus
from app.models.kg.extraction_run import (
    KGExtractionItem,
    KGExtractionItemStatus,
    KGExtractionResetAudit,
    KGExtractionRun,
)
from app.models.kg.mention import PersonMention
from app.models.kg.metrics import SNAGraphMetric
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.models.research import ResearchSession
from app.models.research_group import ResearchGroup, ResearchGroupDocument
from app.services.group_scope import GroupNotFound
from app.services.kg.extraction_run_service import is_mock_model

#: Clock tolerance when comparing a record's ``created_at`` with a run window.
WINDOW_TOLERANCE = timedelta(seconds=5)
#: Databases that must never be a cleanup target, even if the caller asks.
PRODUCTION_DATABASES = frozenset({"ocr_platform", "ocr_platform_recon"})
#: Upper bound on the number of runs a single cleanup may target.
MAX_TARGET_RUNS = 50


class ResetRefused(Exception):
    """The cleanup request is not safe to run (HTTP 400)."""


class ResetConflict(Exception):
    """Nothing can be cleaned under these conditions (HTTP 409)."""


@dataclass
class ResetScope:
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    document_id: Optional[int] = None
    document_name: Optional[str] = None
    document_ids: Tuple[int, ...] = ()
    block_ids: frozenset = frozenset()

    @property
    def label(self) -> str:
        if self.document_id is not None:
            return "文档 #%s（%s）" % (self.document_id, self.document_name or "未命名")
        return "文档组 #%s（%s）" % (self.group_id, self.group_name or "未命名")

    @property
    def confirmation_token(self) -> str:
        return (self.group_name if self.group_id is not None else self.document_name) or ""


@dataclass
class ResetPlan:
    scope: ResetScope
    database_name: str
    target_runs: List[Dict[str, Any]] = field(default_factory=list)
    #: Mentions / evidences that provably belong to a target run and carry no direct
    #: reference: these are the only rows a real execution may delete.
    provable_mentions: List[int] = field(default_factory=list)
    provable_evidence: List[int] = field(default_factory=list)
    #: Mentions / evidences on blocks the target run touched, but whose provenance is
    #: not provable (window mismatch, or the block was also processed by another run).
    ambiguous_mentions: List[int] = field(default_factory=list)
    ambiguous_evidence: List[int] = field(default_factory=list)
    #: Mentions / evidences referenced directly by a saved research snapshot.
    protected_mentions: List[int] = field(default_factory=list)
    protected_evidence: List[int] = field(default_factory=list)
    relationships_deletable: List[int] = field(default_factory=list)
    relationships_protected: List[Dict[str, Any]] = field(default_factory=list)
    #: Relationships that stay but lose (some of) their mock evidence.
    relationships_partially_cleaned: List[int] = field(default_factory=list)
    persons_deletable: List[int] = field(default_factory=list)
    persons_protected: List[Dict[str, Any]] = field(default_factory=list)
    references: Dict[str, int] = field(default_factory=dict)
    shared_documents: List[int] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, Any]:
        return {
            "target_runs": len(self.target_runs),
            "mentions_deletable": len(self.provable_mentions),
            "mentions_ambiguous": len(self.ambiguous_mentions),
            "mentions_protected_referenced": len(self.protected_mentions),
            "evidence_deletable": len(self.provable_evidence),
            "evidence_ambiguous": len(self.ambiguous_evidence),
            "evidence_protected_referenced": len(self.protected_evidence),
            "relationships_deletable": len(self.relationships_deletable),
            "relationships_protected": len(self.relationships_protected),
            "relationships_partially_cleaned": len(self.relationships_partially_cleaned),
            "persons_deletable": len(self.persons_deletable),
            "persons_protected": len(self.persons_protected),
            "shared_documents": len(self.shared_documents),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scope": {
                "group_id": self.scope.group_id,
                "group_name": self.scope.group_name,
                "document_id": self.scope.document_id,
                "document_name": self.scope.document_name,
                "document_count": len(self.scope.document_ids),
                "block_count": len(self.scope.block_ids),
            },
            "database_name": self.database_name,
            "target_runs": self.target_runs,
            "counts": {**self.counts(), **self.references},
            "relationships_protected": self.relationships_protected,
            "relationships_partially_cleaned": self.relationships_partially_cleaned,
            "persons_protected": self.persons_protected,
            "relationships_deletable": self.relationships_deletable,
            "persons_deletable": self.persons_deletable,
            "mentions_deletable": self.provable_mentions,
            "ambiguous_mentions": self.ambiguous_mentions,
            "protected_mentions": self.protected_mentions,
            "evidence_deletable": self.provable_evidence,
            "ambiguous_evidence": self.ambiguous_evidence,
            "protected_evidence": self.protected_evidence,
            "shared_documents": self.shared_documents,
            "notes": self.notes,
        }


# --------------------------------------------------------------------- safety checks
def _database_name(db: Session) -> str:
    return str(db.get_bind().url.database or "")


def assert_cleanup_target_is_safe(db: Session) -> str:
    """Refuse the product database and maintenance databases."""
    name = _database_name(db)
    if not name:
        raise ResetRefused("无法确定当前连接的数据库名，拒绝执行清理。")
    if name in PRODUCTION_DATABASES:
        raise ResetRefused(
            "当前连接的是产品数据库 %r，本功能拒绝在其上执行任何清理。" % name
        )
    if name in FORBIDDEN_DATABASES:
        raise ResetRefused("当前连接的是维护数据库 %r，拒绝执行清理。" % name)
    return name


def resolve_scope(
    db: Session,
    *,
    group_id: Optional[int] = None,
    document_id: Optional[int] = None,
) -> ResetScope:
    if group_id is None and document_id is None:
        raise ResetRefused("必须指定文档组或文档范围。")
    if group_id is not None and document_id is not None:
        raise ResetRefused("一次只能清理一个范围（文档组或单篇文档）。")

    if document_id is not None:
        document = db.query(Document).filter(Document.id == document_id).one_or_none()
        if document is None:
            raise ResetRefused("文档 #%s 不存在。" % document_id)
        blocks = frozenset(_block_ids_for_documents(db, (document.id,)))
        return ResetScope(
            document_id=document.id,
            document_name=document.original_filename or document.filename,
            document_ids=(document.id,),
            block_ids=blocks,
        )

    group = db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
    if group is None:
        raise GroupNotFound("文档组 #%s 不存在或无权访问。" % group_id)
    document_ids = tuple(
        row[0]
        for row in db.query(ResearchGroupDocument.document_id).filter(
            ResearchGroupDocument.group_id == group.id
        )
    )
    return ResetScope(
        group_id=group.id,
        group_name=group.name,
        document_ids=document_ids,
        block_ids=frozenset(_block_ids_for_documents(db, document_ids)),
    )


def _block_ids_for_documents(db: Session, document_ids: Sequence[int]) -> List[int]:
    if not document_ids:
        return []
    return [
        row[0]
        for row in db.query(Block.id)
        .join(Page, Page.id == Block.page_id)
        .filter(Page.document_id.in_(list(document_ids)))
    ]


def _collect_refs(payload: Any, keys: Set[str], out: Set[int]) -> None:
    """Recursively collect integer ids under any of *keys* from a JSON snapshot."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys:
                if isinstance(value, int) and not isinstance(value, bool):
                    out.add(value)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, int) and not isinstance(item, bool):
                            out.add(item)
            _collect_refs(value, keys, out)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _collect_refs(item, keys, out)


BLOCK_REF_KEYS = {"block_id", "block_ids", "source_block_id", "chunk_block_id"}
RELATIONSHIP_REF_KEYS = {"relationship_id", "relationship_ids", "relation_id"}
MENTION_REF_KEYS = {"mention_id", "mention_ids", "person_mention_id"}
EVIDENCE_REF_KEYS = {"evidence_id", "evidence_ids", "relationship_evidence_id"}


@dataclass
class ResearchReferences:
    """What saved research snapshots actually point at.

    A source snapshot stores the *archival* block id plus a copy of the quote; it does
    not contain foreign keys to ``person_mentions`` or ``relationship_evidences``.
    Referencing a block therefore protects the block (never touched here), not the
    derived KG rows that a later extraction wrote onto it.
    """

    blocks: Set[int] = field(default_factory=set)
    relationships: Set[int] = field(default_factory=set)
    mentions: Set[int] = field(default_factory=set)
    evidences: Set[int] = field(default_factory=set)
    session_count: int = 0


def _research_references(db: Session) -> ResearchReferences:
    refs = ResearchReferences()
    sessions = db.query(ResearchSession).all()
    for session in sessions:
        _collect_refs(session.sources, BLOCK_REF_KEYS, refs.blocks)
        _collect_refs(session.relationships, RELATIONSHIP_REF_KEYS, refs.relationships)
        _collect_refs(session.sources, MENTION_REF_KEYS, refs.mentions)
        _collect_refs(session.relationships, MENTION_REF_KEYS, refs.mentions)
        _collect_refs(session.sources, EVIDENCE_REF_KEYS, refs.evidences)
        _collect_refs(session.relationships, EVIDENCE_REF_KEYS, refs.evidences)
    refs.session_count = len(sessions)
    return refs


# --------------------------------------------------------------------------- preview
def build_plan(
    db: Session,
    scope: ResetScope,
    *,
    run_ids: Optional[Sequence[int]] = None,
    only_mock: bool = True,
) -> ResetPlan:
    """Read-only classification of what a cleanup would (and would not) remove."""
    database_name = assert_cleanup_target_is_safe(db)
    plan = ResetPlan(scope=scope, database_name=database_name)

    runs = _candidate_runs(db, scope, run_ids)
    if only_mock:
        runs = [run for run in runs if is_mock_model(run.model)]
    runs = [run for run in runs if run.reset_at is None]
    if not runs:
        raise ResetConflict(
            "该范围内没有可清理的抽取运行记录"
            + ("（已排除非 Mock 模型与已清理过的运行）。" if only_mock else "。")
        )
    if len(runs) > MAX_TARGET_RUNS:
        raise ResetRefused("一次最多清理 %d 条运行记录。" % MAX_TARGET_RUNS)
    target_run_ids = {run.id for run in runs}

    scope_blocks = set(scope.block_ids)
    provable_blocks_for_mentions: Set[int] = set()
    provable_blocks_for_evidence: Set[int] = set()
    touched_blocks: Set[int] = set()
    for run in runs:
        succeeded = {
            row[0]
            for row in db.query(KGExtractionItem.block_id).filter(
                KGExtractionItem.run_id == run.id,
                KGExtractionItem.status == KGExtractionItemStatus.SUCCEEDED.value,
            )
        }
        any_status = {
            row[0]
            for row in db.query(KGExtractionItem.block_id).filter(
                KGExtractionItem.run_id == run.id
            )
        }
        touched_blocks |= (any_status & scope_blocks)
        succeeded &= scope_blocks
        start = run.started_at or run.created_at
        end = run.finished_at or run.updated_at or datetime.utcnow()
        # Mentions and evidences carry their own timestamps, so each gets its own
        # window match: a block can legitimately hold a mention from an earlier run.
        windowed_mentions = {
            row[0]
            for row in db.query(KGExtractionItem.block_id)
            .join(PersonMention, PersonMention.block_id == KGExtractionItem.block_id)
            .filter(
                KGExtractionItem.run_id == run.id,
                KGExtractionItem.status == KGExtractionItemStatus.SUCCEEDED.value,
                PersonMention.created_at >= start - WINDOW_TOLERANCE,
                PersonMention.created_at <= end + WINDOW_TOLERANCE,
            )
        }
        windowed_evidence = {
            row[0]
            for row in db.query(KGExtractionItem.block_id)
            .join(
                RelationshipEvidence,
                RelationshipEvidence.block_id == KGExtractionItem.block_id,
            )
            .filter(
                KGExtractionItem.run_id == run.id,
                KGExtractionItem.status == KGExtractionItemStatus.SUCCEEDED.value,
                RelationshipEvidence.created_at >= start - WINDOW_TOLERANCE,
                RelationshipEvidence.created_at <= end + WINDOW_TOLERANCE,
            )
        }
        provable_blocks_for_mentions |= (succeeded & windowed_mentions)
        provable_blocks_for_evidence |= (succeeded & windowed_evidence)
        plan.target_runs.append(
            {
                "run_id": run.id,
                "status": run.status,
                "model": run.model,
                "is_mock_model": is_mock_model(run.model),
                "trigger": run.trigger,
                "scope_type": run.scope_type,
                "document_id": run.document_id,
                "succeeded_blocks_in_scope": len(succeeded),
                "windowed_mention_blocks": len(windowed_mentions),
                "windowed_evidence_blocks": len(windowed_evidence),
                "budget_blocks": run.budget_blocks,
                "budget_source": run.budget_source,
                "started_at": start.isoformat() if start else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }
        )
        plan.notes.append(
            "run #%s：作用域内成功区块 %d 个；时间窗内提及区块 %d 个、证据区块 %d 个。"
            % (
                run.id,
                len(succeeded),
                len(windowed_mentions),
                len(windowed_evidence),
            )
        )

    # Blocks that another (non-target) run also processed successfully: a genuine
    # record could be mixed in, so these are ambiguous, never provable.
    other_runs = (
        db.query(KGExtractionRun).filter(KGExtractionRun.id.notin_(target_run_ids)).all()
    )
    shared_blocks: Set[int] = set()
    for run in other_runs:
        shared_blocks |= {
            row[0]
            for row in db.query(KGExtractionItem.block_id).filter(
                KGExtractionItem.run_id == run.id,
                KGExtractionItem.status == KGExtractionItemStatus.SUCCEEDED.value,
            )
        }
    shared_blocks &= scope_blocks

    research = _research_references(db)
    plan.references = {
        "research_sessions_scanned": research.session_count,
        "research_blocks_referenced": len(research.blocks & scope_blocks),
        "research_relationships_referenced": len(research.relationships),
        "research_mentions_referenced": len(research.mentions),
        "research_evidences_referenced": len(research.evidences),
    }

    # ------------------------------------------------------------- mentions
    # Provenance is decided *only* by (block processed successfully by a target run)
    # AND (the record was written inside that run's window). A saved research snapshot
    # citing the archival block does not make the derived mention undeletable: the
    # snapshot holds block_id + its own copy of the quote, not a foreign key. Only a
    # snapshot that names the mention id itself protects that mention.
    mention_rows = (
        db.query(PersonMention.id, PersonMention.block_id)
        .filter(PersonMention.block_id.in_(list(touched_blocks) or [-1]))
        .all()
    )
    provable_mentions: List[int] = []
    ambiguous_mentions: List[int] = []
    protected_mentions: List[int] = []
    for mention_id, block_id in mention_rows:
        if mention_id in research.mentions:
            protected_mentions.append(mention_id)
        elif block_id in provable_blocks_for_mentions and block_id not in shared_blocks:
            provable_mentions.append(mention_id)
        else:
            ambiguous_mentions.append(mention_id)
    plan.provable_mentions = provable_mentions
    plan.ambiguous_mentions = ambiguous_mentions
    plan.protected_mentions = protected_mentions

    # --------------------------------------------------------------- evidence
    evidence_rows = (
        db.query(RelationshipEvidence.id, RelationshipEvidence.block_id)
        .filter(RelationshipEvidence.block_id.in_(list(touched_blocks) or [-1]))
        .all()
    )
    provable_evidence: List[int] = []
    ambiguous_evidence: List[int] = []
    protected_evidence_refs: List[int] = []
    for evidence_id, block_id in evidence_rows:
        if evidence_id in research.evidences:
            protected_evidence_refs.append(evidence_id)
        elif block_id in provable_blocks_for_evidence and block_id not in shared_blocks:
            provable_evidence.append(evidence_id)
        else:
            ambiguous_evidence.append(evidence_id)
    plan.provable_evidence = provable_evidence
    plan.ambiguous_evidence = ambiguous_evidence
    plan.protected_evidence = protected_evidence_refs

    deletable_evidence_set = set(provable_evidence)
    evidence_ids_in_scope = [evidence_id for evidence_id, _ in evidence_rows]
    relationship_ids = {
        row[0]
        for row in db.query(RelationshipEvidence.relationship_id)
        .filter(RelationshipEvidence.id.in_(evidence_ids_in_scope or [-1]))
        if row[0] is not None
    }

    # Evidence protection and relationship protection are separate decisions:
    # a relationship that must survive does not force its provable mock evidence to
    # survive with it (the evidence is the false part, the claim is the persistent one).
    for relationship_id in sorted(relationship_ids):
        relationship = (
            db.query(Relationship).filter(Relationship.id == relationship_id).one_or_none()
        )
        if relationship is None:
            continue
        claim = str(getattr(relationship.claim_status, "value", relationship.claim_status))
        if claim == ClaimStatus.ACCEPTED.value:
            plan.relationships_protected.append(
                {"id": relationship_id, "reason": "accepted_claim"}
            )
            continue
        if relationship_id in research.relationships:
            plan.relationships_protected.append(
                {"id": relationship_id, "reason": "referenced_by_research_history"}
            )
            continue
        evidence_ids = [
            row[0]
            for row in db.query(RelationshipEvidence.id).filter(
                RelationshipEvidence.relationship_id == relationship_id
            )
        ]
        deletable_here = [
            evidence_id for evidence_id in evidence_ids if evidence_id in deletable_evidence_set
        ]
        if evidence_ids and len(deletable_here) == len(evidence_ids):
            plan.relationships_deletable.append(relationship_id)
            continue
        if not evidence_ids:
            plan.relationships_protected.append(
                {"id": relationship_id, "reason": "no_evidence_to_verify"}
            )
            continue
        plan.relationships_protected.append(
            {
                "id": relationship_id,
                "reason": "has_unprovable_evidence",
                "deletable_evidence": len(deletable_here),
                "kept_evidence": len(evidence_ids) - len(deletable_here),
            }
        )
        if deletable_here:
            plan.relationships_partially_cleaned.append(relationship_id)

    if plan.relationships_partially_cleaned:
        plan.notes.append(
            "有 %d 条关系本身必须保留，但其下可证明属于目标 Mock 运行的证据仍会单独清理"
            "（关系将失去这些证据，需人工复核关系是否应随后删除）。"
            % len(plan.relationships_partially_cleaned)
        )

    # A claim that survives for a *substantive* reason keeps its provenance: the
    # evidence of an accepted claim is the trace of the human decision, and the evidence
    # of a claim cited by a saved research record is what that record relies on.
    # "Kept because some evidence is unprovable" is different: only the unprovable rows
    # stay, the provably-mock ones are still removed.
    retained_relationship_ids = {
        item["id"]
        for item in plan.relationships_protected
        if item["reason"] in ("accepted_claim", "referenced_by_research_history")
    }
    if retained_relationship_ids:
        retained_evidence = {
            row[0]
            for row in db.query(RelationshipEvidence.id).filter(
                RelationshipEvidence.relationship_id.in_(list(retained_relationship_ids))
            )
        }
        moved = [evidence_id for evidence_id in provable_evidence if evidence_id in retained_evidence]
        if moved:
            plan.provable_evidence = [
                evidence_id for evidence_id in provable_evidence if evidence_id not in retained_evidence
            ]
            plan.protected_evidence = sorted(plan.protected_evidence + moved)
            plan.notes.append(
                "有 %d 条证据属于已接受或被研究记录引用的关系，作为该主张的溯源一并保留。"
                % len(moved)
            )

    # Mentions referenced by evidence that survives cannot be deleted (FK + provenance).
    surviving_evidence = (
        db.query(RelationshipEvidence.person_mention_id)
        .filter(RelationshipEvidence.person_mention_id.isnot(None))
        .filter(
            RelationshipEvidence.id.notin_(list(plan.provable_evidence) or [-1])
        )
    )
    surviving_mention_ids = {row[0] for row in surviving_evidence}
    if surviving_mention_ids:
        kept_mentions = [
            mention_id for mention_id in provable_mentions if mention_id in surviving_mention_ids
        ]
        if kept_mentions:
            plan.provable_mentions = [
                mention_id for mention_id in provable_mentions if mention_id not in surviving_mention_ids
            ]
            plan.protected_mentions = sorted(plan.protected_mentions + kept_mentions)
            plan.notes.append(
                "有 %d 条提及仍被保留下来的证据引用，无法删除，已保护。" % len(kept_mentions)
            )

    # ----------------------------------------------------------------- persons
    deletable_relationships = set(plan.relationships_deletable)
    candidate_person_ids: Set[int] = {
        row[0]
        for row in db.query(PersonMention.person_id)
        .filter(
            PersonMention.id.in_(
                list(set(plan.provable_mentions) | set(plan.ambiguous_mentions)) or [-1]
            ),
            PersonMention.person_id.isnot(None),
        )
    }
    for relationship_id in plan.relationships_deletable:
        relationship = db.query(Relationship).filter(Relationship.id == relationship_id).one()
        candidate_person_ids.add(relationship.source_person_id)
        candidate_person_ids.add(relationship.target_person_id)

    provable_mentions_set = set(plan.provable_mentions)
    for person_id in sorted(candidate_person_ids):
        person = db.query(Person).filter(Person.id == person_id).one_or_none()
        if person is None:
            continue
        identity = str(
            getattr(person.identity_status, "value", person.identity_status)
        )
        if identity == IdentityStatus.ACCEPTED.value:
            plan.persons_protected.append({"id": person_id, "reason": "accepted_identity"})
            continue
        if db.query(EntityOperationAudit.id).filter(
            (EntityOperationAudit.primary_person_id == person_id)
            | (EntityOperationAudit.secondary_person_id == person_id)
        ).first():
            plan.persons_protected.append({"id": person_id, "reason": "has_entity_audit"})
            continue
        if db.query(ExternalAuthority.id).filter(
            ExternalAuthority.person_id == person_id
        ).first():
            plan.persons_protected.append({"id": person_id, "reason": "has_external_authority"})
            continue
        if db.query(SNAGraphMetric.id).filter(SNAGraphMetric.person_id == person_id).first():
            plan.persons_protected.append({"id": person_id, "reason": "has_sna_metric"})
            continue
        remaining_mentions = [
            row[0]
            for row in db.query(PersonMention.id).filter(
                PersonMention.person_id == person_id,
                PersonMention.id.notin_(list(provable_mentions_set) or [-1]),
            )
        ]
        if remaining_mentions:
            plan.persons_protected.append(
                {"id": person_id, "reason": "still_has_mentions_outside_target_runs"}
            )
            continue
        remaining_relationships = [
            row[0]
            for row in db.query(Relationship.id).filter(
                (Relationship.source_person_id == person_id)
                | (Relationship.target_person_id == person_id),
                Relationship.id.notin_(list(deletable_relationships) or [-1]),
            )
        ]
        if remaining_relationships:
            plan.persons_protected.append(
                {"id": person_id, "reason": "still_has_relationships"}
            )
            continue
        plan.persons_deletable.append(person_id)

    # ------------------------------------------------------- shared documents
    if scope.document_ids:
        plan.shared_documents = sorted(
            row[0]
            for row in db.query(ResearchGroupDocument.document_id)
            .filter(
                ResearchGroupDocument.document_id.in_(list(scope.document_ids)),
                (ResearchGroupDocument.group_id != scope.group_id)
                if scope.group_id is not None
                else (ResearchGroupDocument.group_id.isnot(None)),
            )
            .distinct()
        )
        if plan.shared_documents:
            plan.notes.append(
                "以下文档同时属于其他文档组，清理会影响那些组的图谱：%s"
                % ", ".join(str(doc_id) for doc_id in plan.shared_documents)
            )

    if plan.ambiguous_mentions or plan.ambiguous_evidence:
        plan.notes.append(
            "存在 %d 条提及和 %d 条证据无法证明归属于目标运行（时间窗不匹配，或区块被其他运行处理过），"
            "已列为来源不确定，不会删除。"
            % (len(plan.ambiguous_mentions), len(plan.ambiguous_evidence))
        )
    if plan.protected_mentions or plan.protected_evidence:
        plan.notes.append(
            "有 %d 条提及和 %d 条证据被已保存的研究记录直接引用（按 id），已保护。"
            % (len(plan.protected_mentions), len(plan.protected_evidence))
        )
    cited_blocks = sorted(research.blocks & scope_blocks)
    if cited_blocks:
        cited_evidence = sum(
            1 for evidence_id, block_id in evidence_rows if block_id in research.blocks
        )
        cited_mentions = sum(
            1 for mention_id, block_id in mention_rows if block_id in research.blocks
        )
        plan.references["research_referenced_blocks_in_scope"] = len(cited_blocks)
        plan.references["research_cited_blocks_with_mock_records"] = len(
            {
                block_id
                for _, block_id in list(evidence_rows) + list(mention_rows)
                if block_id in research.blocks
            }
        )
        plan.notes.append(
            "有 %d 个区块被已保存的研究记录引用（引用的是原始区块文本与引文副本，不是派生的提及/证据）："
            "区块 %s 必须保留且不会被修改；其上 %d 条提及、%d 条证据按同一归属规则判定，"
            "可证明属于目标 Mock 运行的部分仍会清理，研究记录本身不受影响。"
            % (
                len(cited_blocks),
                ", ".join(str(block_id) for block_id in cited_blocks[:10]),
                cited_mentions,
                cited_evidence,
            )
        )
    return plan


def _candidate_runs(
    db: Session, scope: ResetScope, run_ids: Optional[Sequence[int]]
) -> List[KGExtractionRun]:
    query = db.query(KGExtractionRun)
    if scope.group_id is not None:
        query = query.filter(KGExtractionRun.group_id == scope.group_id)
    else:
        query = query.filter(KGExtractionRun.document_id == scope.document_id)
    runs = query.order_by(KGExtractionRun.id.desc()).all()
    if run_ids:
        wanted = set(int(value) for value in run_ids)
        selected = [run for run in runs if run.id in wanted]
        missing = wanted - {run.id for run in selected}
        if missing:
            raise ResetRefused(
                "运行记录 %s 不属于该范围，已拒绝。" % ", ".join(str(v) for v in sorted(missing))
            )
        return selected
    return runs


# -------------------------------------------------------------------------- execution
def _delete_plan(db: Session, plan: ResetPlan) -> Dict[str, int]:
    deleted: Dict[str, int] = {}
    if plan.provable_evidence:
        deleted["relationship_evidences"] = (
            db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.id.in_(plan.provable_evidence))
            .delete(synchronize_session=False)
        )
    if plan.relationships_deletable:
        deleted["relationships"] = (
            db.query(Relationship)
            .filter(Relationship.id.in_(plan.relationships_deletable))
            .delete(synchronize_session=False)
        )
    if plan.provable_mentions:
        deleted["person_mentions"] = (
            db.query(PersonMention)
            .filter(PersonMention.id.in_(plan.provable_mentions))
            .delete(synchronize_session=False)
        )
    if plan.persons_deletable:
        deleted["persons"] = (
            db.query(Person)
            .filter(Person.id.in_(plan.persons_deletable))
            .delete(synchronize_session=False)
        )
    return deleted


def execute_reset(
    db: Session,
    *,
    group_id: Optional[int] = None,
    document_id: Optional[int] = None,
    run_ids: Optional[Sequence[int]] = None,
    dry_run: bool = True,
    confirm: Optional[str] = None,
    reason: Optional[str] = None,
    operator: str = "system",
) -> Dict[str, Any]:
    """Preview (default) or execute the cleanup.

    Execution rules:
    * ``confirm`` must equal the scope's name (group name / document filename);
    * ``reason`` is mandatory;
    * the target database must not be a product/maintenance database;
    * everything happens in one transaction that rolls back on any error.
    """
    if not dry_run:
        scope_for_token = resolve_scope(db, group_id=group_id, document_id=document_id)
        expected = scope_for_token.confirmation_token
        if not confirm or confirm.strip() != expected:
            raise ResetRefused(
                "执行清理必须提供完全匹配的确认名称：%r。" % expected
            )
        if not reason or not reason.strip():
            raise ResetRefused("执行清理必须提供原因（reason）。")

    scope = resolve_scope(db, group_id=group_id, document_id=document_id)
    plan = build_plan(db, scope, run_ids=run_ids)
    result: Dict[str, Any] = {
        "dry_run": dry_run,
        **plan.as_dict(),
    }
    if dry_run:
        result["deleted"] = {}
        return result

    try:
        deleted = _delete_plan(db, plan)
        now = datetime.utcnow()
        for run_id in [entry["run_id"] for entry in plan.target_runs]:
            run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
            if run is not None:
                run.reset_at = now
                run.reset_reason = reason
        audit = KGExtractionResetAudit(
            group_id=scope.group_id,
            document_id=scope.document_id,
            dry_run=False,
            run_ids=[entry["run_id"] for entry in plan.target_runs],
            counts={**plan.counts(), **deleted},
            reason=reason,
            operator=operator,
        )
        db.add(audit)
        db.commit()
    except Exception:
        db.rollback()
        logger.error("Extraction reset rolled back for %s", scope.label, exc_info=True)
        raise

    logger.info("Extraction reset executed for %s: %s", scope.label, deleted)
    result["deleted"] = deleted
    result["reset_at"] = now.isoformat()
    return result
