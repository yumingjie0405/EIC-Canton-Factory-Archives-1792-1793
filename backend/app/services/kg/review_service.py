"""Relationship review and mention disambiguation as an auditable workflow.

Two rules shape everything here:

1. **Provenance is never upgraded by a review.** Accepting a mock-derived claim marks
   it accepted *as a claim in this tool*; it keeps its ``mock_*`` source kind and the
   UI must keep saying so. The service therefore returns provenance next to every row
   and refuses to pretend a decision changed the origin.
2. **Batches are previewed, then confirmed, then revertible.** A batch writes one
   ``kg_review_audit`` row per group of changes with a shared ``batch_id``; reverting
   restores the previous ``person_id`` / ``resolution_status`` / ``review_status``
   recorded in the audit rows. Nothing is applied without an explicit confirmation
   token derived from the previewed content.
"""
import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import logger
from app.models.kg.enums import (
    ClaimStatus,
    IdentityStatus,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
    SourceKind,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.kg.workflow import KGReviewAudit
from app.services.kg import provenance
from app.services.kg.group_graph import GroupGraphScope, load_scope

#: Statuses the review API accepts. They mirror the stored enums exactly; the API never
#: invents a status the model cannot represent.
REVIEWABLE_CLAIM_STATUSES = (
    ClaimStatus.ASSERTED.value,
    ClaimStatus.ACCEPTED.value,
    ClaimStatus.UNCERTAIN.value,
    ClaimStatus.CONTESTED.value,
    ClaimStatus.REJECTED.value,
)
REVIEWABLE_REVIEW_STATUSES = (
    ReviewStatusKG.UNREVIEWED.value,
    ReviewStatusKG.IN_REVIEW.value,
    ReviewStatusKG.REVIEWED.value,
)

#: Upper bound for one preview/confirm call so a typo cannot try to relink the archive.
MAX_BATCH_MENTIONS = 500


class ReviewError(Exception):
    """The requested review action is not allowed (HTTP 400/409)."""


def _evidence_kinds(db: Session, relationship_ids: Sequence[int]) -> Dict[int, List[str]]:
    if not relationship_ids:
        return {}
    rows = (
        db.query(RelationshipEvidence.relationship_id, RelationshipEvidence.source_kind)
        .filter(RelationshipEvidence.relationship_id.in_(list(relationship_ids)))
        .all()
    )
    out: Dict[int, List[str]] = {}
    for relationship_id, kind in rows:
        out.setdefault(relationship_id, []).append(kind or SourceKind.UNKNOWN.value)
    return out


def _evidence_counts(db: Session, relationship_ids: Sequence[int]) -> Dict[int, int]:
    if not relationship_ids:
        return {}
    rows = (
        db.query(RelationshipEvidence.relationship_id, func.count(RelationshipEvidence.id))
        .filter(RelationshipEvidence.relationship_id.in_(list(relationship_ids)))
        .group_by(RelationshipEvidence.relationship_id)
        .all()
    )
    return {relationship_id: count for relationship_id, count in rows}


def relationship_row(
    relationship: Relationship,
    source_name: Optional[str],
    target_name: Optional[str],
    evidence_count: int,
    kinds: Sequence[str],
) -> Dict[str, Any]:
    """One reviewable relationship, with provenance and review state kept separate."""
    combined = provenance.combine(kinds)
    claim = str(getattr(relationship.claim_status, "value", relationship.claim_status))
    review = str(getattr(relationship.review_status, "value", relationship.review_status))
    synthetic = any(provenance.is_synthetic(kind) for kind in kinds)
    return {
        "id": relationship.id,
        "source_person_id": relationship.source_person_id,
        "target_person_id": relationship.target_person_id,
        "source_name": source_name,
        "target_name": target_name,
        "relation_type": str(
            getattr(relationship.relation_type, "value", relationship.relation_type)
        ),
        "relation_label": relationship.relation_label,
        "claim_status": claim,
        "review_status": review,
        "evidence_count": evidence_count,
        "has_date": bool(relationship.valid_from or relationship.valid_to or relationship.time_description),
        "valid_from": relationship.valid_from.isoformat() if relationship.valid_from else None,
        "valid_to": relationship.valid_to.isoformat() if relationship.valid_to else None,
        "time_description": relationship.time_description,
        # Three independent facts, never collapsed into one badge:
        "source_kind": combined,
        "source_kind_label": provenance.describe(combined),
        "source_kinds": provenance.summarise(kinds),
        "synthetic": synthetic or not kinds,
        "unverified": synthetic or not kinds or any(
            provenance.is_unverified(kind) for kind in kinds
        ),
        "reviewable": claim != ClaimStatus.ACCEPTED.value or review == ReviewStatusKG.UNREVIEWED.value,
    }


def list_group_relationships(
    db: Session,
    group_id: int,
    *,
    claim_status: Optional[str] = None,
    review_status: Optional[str] = None,
    relation_type: Optional[str] = None,
    source_kind: Optional[str] = None,
    has_date: Optional[bool] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """Evidence-first candidate list for one group.

    Only relationships with in-group evidence are returned: a global claim that this
    group's documents do not support is not a candidate here.
    """
    scope: GroupGraphScope = load_scope(db, group_id)
    if not scope.relationship_ids:
        return {
            "group_id": scope.group_id,
            "group_name": scope.group_name,
            "total": 0,
            "items": [],
            "counts": {"by_claim_status": {}, "by_source_kind": {}, "by_relation_type": {}},
            "data_trust": provenance.trust_payload([]),
        }

    query = db.query(Relationship).filter(Relationship.id.in_(list(scope.relationship_ids)))
    if claim_status:
        query = query.filter(Relationship.claim_status == claim_status)
    if review_status:
        query = query.filter(Relationship.review_status == review_status)
    if relation_type:
        query = query.filter(Relationship.relation_type == relation_type)
    if has_date is True:
        query = query.filter(
            or_(
                Relationship.valid_from.isnot(None),
                Relationship.valid_to.isnot(None),
                Relationship.time_description.isnot(None),
            )
        )
    elif has_date is False:
        query = query.filter(
            Relationship.valid_from.is_(None),
            Relationship.valid_to.is_(None),
            Relationship.time_description.is_(None),
        )

    all_rows = query.all()
    names = {
        person.id: person.display_name
        for person in db.query(Person)
        .filter(
            Person.id.in_(
                {row.source_person_id for row in all_rows}
                | {row.target_person_id for row in all_rows}
                or {0}
            )
        )
        .all()
    }
    counts_by_id = _evidence_counts(db, [row.id for row in all_rows])
    kinds_by_id = _evidence_kinds(db, [row.id for row in all_rows])

    items = [
        relationship_row(
            row,
            names.get(row.source_person_id),
            names.get(row.target_person_id),
            counts_by_id.get(row.id, 0),
            kinds_by_id.get(row.id, []),
        )
        for row in all_rows
    ]

    if q:
        needle = q.strip().lower()
        items = [
            item
            for item in items
            if needle
            in " ".join(
                filter(
                    None,
                    [
                        str(item["source_name"] or ""),
                        str(item["target_name"] or ""),
                        str(item["relation_label"] or ""),
                    ],
                )
            ).lower()
        ]
    if source_kind:
        items = [
            item
            for item in items
            if source_kind == SourceKind.UNKNOWN.value
            and item["source_kind"] == SourceKind.UNKNOWN.value
            or source_kind in item["source_kinds"]
        ]

    # Counts describe the *unpaginated* filtered set, so a UI chip never disagrees
    # with the list it belongs to.
    by_claim: Dict[str, int] = {}
    by_review: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for item in items:
        by_claim[item["claim_status"]] = by_claim.get(item["claim_status"], 0) + 1
        by_review[item["review_status"]] = by_review.get(item["review_status"], 0) + 1
        by_type[item["relation_type"]] = by_type.get(item["relation_type"], 0) + 1
        for kind, count in item["source_kinds"].items():
            by_source[kind] = by_source.get(kind, 0) + count

    page = items[skip : skip + max(1, min(limit, 200))]
    return {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "total": len(items),
        "skip": skip,
        "limit": limit,
        "items": page,
        "counts": {
            "by_claim_status": by_claim,
            "by_review_status": by_review,
            "by_relation_type": by_type,
            "by_source_kind": by_source,
        },
        "data_trust": provenance.trust_payload(
            [kind for item in items for kind in item["source_kinds"] for _ in range(0, 1)]
        ),
    }


def review_relationship(
    db: Session,
    relationship_id: int,
    *,
    claim_status: Optional[str] = None,
    review_status: Optional[str] = None,
    reason: Optional[str] = None,
    operator: str = "system",
    evidence_note: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist one human decision about a claim, with before/after in the audit row."""
    relationship = db.query(Relationship).filter(Relationship.id == relationship_id).one_or_none()
    if relationship is None:
        raise ReviewError("关系 #%s 不存在。" % relationship_id)
    if claim_status is not None and claim_status not in REVIEWABLE_CLAIM_STATUSES:
        raise ReviewError("不支持的主张状态：%s" % claim_status)
    if review_status is not None and review_status not in REVIEWABLE_REVIEW_STATUSES:
        raise ReviewError("不支持的审核状态：%s" % review_status)
    if claim_status is None and review_status is None and not evidence_note:
        raise ReviewError("没有需要变更的内容。")

    before = {
        "claim_status": str(
            getattr(relationship.claim_status, "value", relationship.claim_status)
        ),
        "review_status": str(
            getattr(relationship.review_status, "value", relationship.review_status)
        ),
        "evidence_summary": relationship.evidence_summary,
    }

    if review_status is not None:
        relationship.review_status = ReviewStatusKG(review_status)
    if claim_status is not None:
        relationship.claim_status = ClaimStatus(claim_status)
    if evidence_note:
        existing = relationship.evidence_summary or ""
        relationship.evidence_summary = (
            "%s\n[%s]: %s" % (existing, operator, evidence_note)
        ).strip()

    after = {
        "claim_status": str(
            getattr(relationship.claim_status, "value", relationship.claim_status)
        ),
        "review_status": str(
            getattr(relationship.review_status, "value", relationship.review_status)
        ),
        "evidence_summary": relationship.evidence_summary,
    }

    kinds = [
        row[0] or SourceKind.UNKNOWN.value
        for row in db.query(RelationshipEvidence.source_kind).filter(
            RelationshipEvidence.relationship_id == relationship_id
        )
    ]
    combined = provenance.combine(kinds)

    audit = KGReviewAudit(
        operation_type="relationship_review",
        relationship_id=relationship_id,
        before=before,
        after=after,
        reason=reason,
        operator=operator,
        affected_counts={"evidence_rows": len(kinds)},
    )
    db.add(audit)
    db.commit()
    db.refresh(relationship)
    logger.info(
        "Relationship %s reviewed by %s: %s -> %s (source_kind=%s)",
        relationship_id,
        operator,
        before,
        after,
        combined,
    )

    count = _evidence_counts(db, [relationship_id]).get(relationship_id, 0)
    synthetic = any(provenance.is_synthetic(kind) for kind in kinds)
    unverified = any(provenance.is_unverified(kind) for kind in kinds) or not kinds
    return {
        "relationship": relationship_row(relationship, None, None, count, kinds),
        "audit_id": audit.id,
        "source_kind": combined,
        "source_kind_label": provenance.describe(combined),
        "synthetic": synthetic,
        "unverified": unverified,
        "warning": (
            "该关系由规则桩/固定 Mock 生成：审核结论只表示本项目内部的处理状态，"
            "不构成档案证据，也不会改变其来源标签。"
            if synthetic
            else (
                "该关系的证据来源无法重建（早于抽取台账）：审核结论只表示本项目内部的处理状态。"
                if unverified
                else None
            )
        ),
    }


def review_history(db: Session, *, relationship_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    query = db.query(KGReviewAudit)
    if relationship_id is not None:
        query = query.filter(KGReviewAudit.relationship_id == relationship_id)
    rows = query.order_by(KGReviewAudit.id.desc()).limit(max(1, min(limit, 200))).all()
    return [
        {
            "id": row.id,
            "operation_type": row.operation_type,
            "relationship_id": row.relationship_id,
            "person_id": row.person_id,
            "batch_id": row.batch_id,
            "before": row.before,
            "after": row.after,
            "reason": row.reason,
            "operator": row.operator,
            "is_reverted": row.is_reverted,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


# ---------------------------------------------------------------- disambiguation
def mention_clusters(
    db: Session,
    group_id: int,
    *,
    only_unresolved: bool = True,
    source_kind: Optional[str] = None,
    limit_groups: int = 50,
    samples: int = 3,
) -> Dict[str, Any]:
    """Group in-scope mentions by normalized text so a human can decide per name.

    Same normalized text is **not** treated as the same person: the cluster is a
    candidate for review, with its source distribution and sample contexts attached.
    """
    scope = load_scope(db, group_id)
    empty = {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "total_mentions": 0,
        "total_clusters": 0,
        "items": [],
        "data_trust": provenance.trust_payload([]),
    }
    if not scope.block_ids:
        return empty

    query = db.query(PersonMention).filter(PersonMention.block_id.in_(list(scope.block_ids)))
    if only_unresolved:
        query = query.filter(PersonMention.person_id.is_(None))
    query = query.filter(PersonMention.resolution_status != ResolutionStatus.REJECTED)
    mentions = query.all()
    if source_kind:
        mentions = [
            mention
            for mention in mentions
            if (mention.source_kind or SourceKind.UNKNOWN.value) == source_kind
        ]

    clusters: Dict[str, List[PersonMention]] = {}
    for mention in mentions:
        key = (mention.normalized_text or mention.raw_text or "").strip()
        clusters.setdefault(key, []).append(mention)

    person_ids = {mention.person_id for mention in mentions if mention.person_id}
    person_names = {
        person.id: person.display_name
        for person in db.query(Person).filter(Person.id.in_(person_ids or {0})).all()
    }

    items = []
    for key, members in sorted(clusters.items(), key=lambda item: len(item[1]), reverse=True)[
        : max(1, min(limit_groups, 200))
    ]:
        kind_counts = provenance.summarise(mention.source_kind for mention in members)
        existing = sorted({person_names.get(m.person_id, "#%s" % m.person_id) for m in members if m.person_id})
        items.append(
            {
                "normalized_text": key,
                "mention_count": len(members),
                "block_count": len({m.block_id for m in members}),
                "resolved_person_names": existing,
                "source_kinds": kind_counts,
                "synthetic": any(provenance.is_synthetic(m.source_kind) for m in members),
                "unverified": any(provenance.is_unverified(m.source_kind) for m in members),
                "sample_mentions": [
                    {
                        "mention_id": m.id,
                        "raw_text": m.raw_text,
                        "block_id": m.block_id,
                        "temporal_year": m.temporal_year,
                        "evidence_text": (m.evidence_text or "")[:200],
                        "source_kind": m.source_kind or SourceKind.UNKNOWN.value,
                    }
                    for m in sorted(members, key=lambda m: m.id)[: max(1, samples)]
                ],
            }
        )

    return {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "total_mentions": len(mentions),
        "total_clusters": len(clusters),
        "items": items,
        "data_trust": provenance.trust_payload(mention.source_kind for mention in mentions),
    }


def _cluster_mentions(
    db: Session, scope: GroupGraphScope, normalized_text: str
) -> List[PersonMention]:
    if not scope.block_ids:
        return []
    return (
        db.query(PersonMention)
        .filter(
            PersonMention.block_id.in_(list(scope.block_ids)),
            PersonMention.person_id.is_(None),
            PersonMention.resolution_status != ResolutionStatus.REJECTED,
            func.lower(func.coalesce(PersonMention.normalized_text, PersonMention.raw_text))
            == normalized_text.strip().lower(),
        )
        .order_by(PersonMention.id.asc())
        .all()
    )


def _preview_token(group_id: int, normalized_text: str, person_id: int, mention_ids: Sequence[int]) -> str:
    payload = "|".join(
        [str(group_id), normalized_text.strip().lower(), str(person_id)]
        + [str(value) for value in mention_ids]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def preview_batch_link(
    db: Session,
    group_id: int,
    *,
    normalized_text: str,
    person_id: Optional[int] = None,
    new_person_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Preview linking every unresolved mention of one name to one person.

    The preview is the contract: it returns the exact mention ids, the affected counts,
    the provenance mix and a token that the confirm call must echo. Nothing is written.
    """
    scope = load_scope(db, group_id)
    if not normalized_text or not normalized_text.strip():
        raise ReviewError("必须提供要处理的规范化名称。")

    mentions = _cluster_mentions(db, scope, normalized_text)
    if not mentions:
        raise ReviewError("该名称在本组没有待处理的提及。")
    if len(mentions) > MAX_BATCH_MENTIONS:
        raise ReviewError(
            "本次命中 %d 条提及，超过单次上限 %d 条，请缩小范围。"
            % (len(mentions), MAX_BATCH_MENTIONS)
        )

    target = None
    if person_id is not None:
        target = db.query(Person).filter(Person.id == person_id).one_or_none()
        if target is None:
            raise ReviewError("目标人物 #%s 不存在。" % person_id)
    elif not (new_person_name or "").strip():
        raise ReviewError("必须指定已有的人物，或提供新人物名称。")

    kind_counts = provenance.summarise(mention.source_kind for mention in mentions)
    warnings: List[str] = []
    if any(provenance.is_synthetic(mention.source_kind) for mention in mentions):
        warnings.append(
            "这些提及来自固定 Mock / 规则桩：可以验证消歧流程，但不能作为历史人物识别结论。"
        )
    elif any(provenance.is_unverified(mention.source_kind) for mention in mentions):
        warnings.append(
            "部分提及的来源无法重建（早于抽取台账）：批量关联只表示本项目内的处理决定，"
            "不能视为已核实的身份识别。"
        )
    years = sorted({m.temporal_year for m in mentions if m.temporal_year})
    if len(years) > 1:
        warnings.append("命中提及跨越多个年份：%s，请确认是否同一人。" % "、".join(str(y) for y in years))
    documents = len({m.block_id for m in mentions})
    if documents > 1:
        warnings.append("命中提及分布在 %d 个不同区块，请抽查上下文。" % documents)

    mention_ids = [mention.id for mention in mentions]
    return {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "normalized_text": normalized_text,
        "target_person_id": target.id if target else None,
        "target_person_name": target.display_name if target else new_person_name.strip(),
        "create_new_person": target is None,
        "mention_count": len(mentions),
        "mention_ids": mention_ids,
        "years": years,
        "source_kinds": kind_counts,
        "synthetic": any(provenance.is_synthetic(mention.source_kind) for mention in mentions),
        "unverified": any(provenance.is_unverified(mention.source_kind) for mention in mentions),
        "warnings": warnings,
        "confirm_token": _preview_token(
            scope.group_id, normalized_text, target.id if target else 0, mention_ids
        ),
    }


def confirm_batch_link(
    db: Session,
    group_id: int,
    *,
    normalized_text: str,
    confirm_token: str,
    person_id: Optional[int] = None,
    new_person_name: Optional[str] = None,
    reason: Optional[str] = None,
    operator: str = "system",
) -> Dict[str, Any]:
    """Apply a previewed batch. Refuses when the underlying set has changed."""
    preview = preview_batch_link(
        db,
        group_id,
        normalized_text=normalized_text,
        person_id=person_id,
        new_person_name=new_person_name,
    )
    if confirm_token != preview["confirm_token"]:
        raise ReviewError("预览已过期或与当前数据不一致，请重新预览后再确认。")

    target = None
    created_person = False
    if person_id is not None:
        target = db.query(Person).filter(Person.id == person_id).one_or_none()
    if target is None:
        name = (new_person_name or normalized_text).strip()
        existing = db.query(Person).filter(Person.display_name.ilike(name)).one_or_none()
        if existing is not None:
            target = existing
        else:
            target = Person(
                display_name=name,
                canonical_name_en=name,
                identity_status=IdentityStatus.PROVISIONAL,
                review_status=ReviewStatusKG.UNREVIEWED,
            )
            db.add(target)
            db.flush()
            created_person = True

    batch_id = uuid.uuid4().hex
    audit_rows = []
    for mention_id in preview["mention_ids"]:
        mention = db.query(PersonMention).filter(PersonMention.id == mention_id).one_or_none()
        if mention is None:
            continue
        before = {
            "person_id": mention.person_id,
            "resolution_status": str(
                getattr(mention.resolution_status, "value", mention.resolution_status)
            ),
            "review_status": str(
                getattr(mention.review_status, "value", mention.review_status)
            ),
        }
        mention.person_id = target.id
        mention.resolution_status = ResolutionStatus.RESOLVED
        mention.review_status = ReviewStatusKG.REVIEWED
        after = {
            "person_id": target.id,
            "resolution_status": ResolutionStatus.RESOLVED.value,
            "review_status": ReviewStatusKG.REVIEWED.value,
        }
        audit = KGReviewAudit(
            operation_type="mention_batch_link",
            group_id=group_id,
            person_id=target.id,
            mention_id=mention.id,
            batch_id=batch_id,
            before=before,
            after=after,
            reason=reason,
            operator=operator,
        )
        db.add(audit)
        audit_rows.append(audit)

    db.commit()
    logger.info(
        "Batch mention link applied: group=%s name=%s person=%s mentions=%d batch=%s operator=%s",
        group_id,
        normalized_text,
        target.id,
        len(audit_rows),
        batch_id,
        operator,
    )
    return {
        "batch_id": batch_id,
        "person_id": target.id,
        "person_name": target.display_name,
        "created_person": created_person,
        "mentions_linked": len(audit_rows),
        "synthetic": preview["synthetic"],
        "source_kinds": preview["source_kinds"],
        "warnings": preview["warnings"],
        "audit_ids": [row.id for row in audit_rows],
    }


def revert_batch(
    db: Session, batch_id: str, *, reason: Optional[str] = None, operator: str = "system"
) -> Dict[str, Any]:
    """Undo one batch by restoring the ``before`` state recorded per mention."""
    rows = (
        db.query(KGReviewAudit)
        .filter(KGReviewAudit.batch_id == batch_id, KGReviewAudit.is_reverted.is_(False))
        .order_by(KGReviewAudit.id.desc())
        .all()
    )
    if not rows:
        raise ReviewError("批次 %s 不存在或已撤销。" % batch_id)

    restored = 0
    for row in rows:
        if row.mention_id is None:
            continue
        mention = (
            db.query(PersonMention).filter(PersonMention.id == row.mention_id).one_or_none()
        )
        if mention is None:
            row.is_reverted = True
            row.reverted_at = datetime.utcnow()
            continue
        before = row.before or {}
        mention.person_id = before.get("person_id")
        status_value = before.get("resolution_status")
        if status_value:
            mention.resolution_status = ResolutionStatus(status_value)
        review_value = before.get("review_status")
        if review_value:
            mention.review_status = ReviewStatusKG(review_value)
        row.is_reverted = True
        row.reverted_at = datetime.utcnow()
        restored += 1

    db.add(
        KGReviewAudit(
            operation_type="mention_batch_revert",
            batch_id=batch_id,
            before={"reverted_rows": len(rows)},
            after={"restored_mentions": restored},
            reason=reason,
            operator=operator,
        )
    )
    db.commit()
    return {"batch_id": batch_id, "restored_mentions": restored, "reverted_audit_rows": len(rows)}
