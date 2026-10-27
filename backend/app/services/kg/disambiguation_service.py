"""Disambiguation and human-in-the-loop entity resolution service."""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core import logger, AppException
from app.models.kg.audit import EntityOperationAudit
from app.models.kg.enums import ResolutionStatus, ReviewStatusKG
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.services.kg.candidate_generator import candidate_generator
from app.services.kg.entity_matcher import MatchEvaluation, entity_matcher


class DisambiguationService:
    """Manages mention resolution workflow, candidate evaluation, and human review decisions."""

    def evaluate_mention_candidates(
        self,
        db: Session,
        mention_id: int,
    ) -> Tuple[PersonMention, List[Dict[str, Any]]]:
        """Generate candidates and compute multi-dimensional explainable evaluations for a mention."""
        mention = db.query(PersonMention).filter_by(id=mention_id).first()
        if not mention:
            raise AppException(f"PersonMention {mention_id} not found", code="MENTION_NOT_FOUND")

        candidates_scored = candidate_generator.generate_candidates(db=db, mention=mention)
        evaluations: List[Dict[str, Any]] = []

        cand_ids = []
        for person, pre_score in candidates_scored:
            cand_ids.append(person.id)
            evaluation: MatchEvaluation = entity_matcher.evaluate_match(
                db=db, mention=mention, candidate=person
            )
            evaluations.append({
                "person_id": person.id,
                "display_name": person.display_name,
                "canonical_name_en": person.canonical_name_en,
                "canonical_name_zh": person.canonical_name_zh,
                "primary_role": person.primary_role,
                "preliminary_similarity": pre_score,
                "name_score": evaluation.name_score,
                "temporal_score": evaluation.temporal_score,
                "spatial_score": evaluation.spatial_score,
                "role_score": evaluation.role_score,
                "context_score": evaluation.context_score,
                "authority_score": evaluation.authority_score,
                "total_score": evaluation.total_score,
                "supporting_evidence": evaluation.supporting_evidence,
                "contradiction_flags": evaluation.contradiction_flags,
                "recommendation": evaluation.recommendation,
            })

        # Update candidate_person_ids on the mention
        mention.candidate_person_ids = cand_ids
        if mention.resolution_status == ResolutionStatus.UNRESOLVED and cand_ids:
            mention.resolution_status = ResolutionStatus.CANDIDATE
        db.flush()

        # Sort evaluations by total score
        evaluations.sort(key=lambda x: x["total_score"], reverse=True)
        return mention, evaluations

    def confirm_resolution(
        self,
        db: Session,
        mention_id: int,
        person_id: int,
        reviewer: str = "curator",
        reason: Optional[str] = None,
    ) -> PersonMention:
        """Confirm association between a mention and a canonical Person with audit trail."""
        mention = db.query(PersonMention).filter_by(id=mention_id).first()
        if not mention:
            raise AppException(f"PersonMention {mention_id} not found", code="MENTION_NOT_FOUND")

        person = db.query(Person).filter_by(id=person_id).first()
        if not person:
            raise AppException(f"Person {person_id} not found", code="PERSON_NOT_FOUND")

        old_person_id = mention.person_id
        old_status = mention.resolution_status.value

        mention.person_id = person.id
        mention.resolution_status = ResolutionStatus.RESOLVED
        mention.review_status = ReviewStatusKG.REVIEWED
        mention.updated_at = datetime.utcnow()

        # Record audit log
        audit_entry = EntityOperationAudit(
            operation_type="confirm_link",
            primary_person_id=person.id,
            secondary_person_id=old_person_id,
            snapshot_details={
                "mention_id": mention.id,
                "raw_text": mention.raw_text,
                "previous_person_id": old_person_id,
                "previous_resolution_status": old_status,
            },
            reason=reason or f"Confirmed mention '{mention.raw_text}' to Person '{person.display_name}'",
            operator=reviewer,
        )
        db.add(audit_entry)
        db.commit()

        logger.info(
            "Confirmed resolution: mention_id=%s -> person_id=%s (reviewer=%s)",
            mention_id, person_id, reviewer
        )
        return mention

    def reject_candidate(
        self,
        db: Session,
        mention_id: int,
        person_id: Optional[int] = None,
        reviewer: str = "curator",
        reason: Optional[str] = None,
    ) -> PersonMention:
        """Reject a candidate Person for a mention or reject mention outright."""
        mention = db.query(PersonMention).filter_by(id=mention_id).first()
        if not mention:
            raise AppException(f"PersonMention {mention_id} not found", code="MENTION_NOT_FOUND")

        # If person_id provided, remove from candidate list
        current_cands = mention.candidate_person_ids or []
        if person_id and person_id in current_cands:
            current_cands = [cid for cid in current_cands if cid != person_id]
            mention.candidate_person_ids = current_cands

        if not current_cands:
            mention.resolution_status = ResolutionStatus.UNCERTAIN

        mention.updated_at = datetime.utcnow()

        audit_entry = EntityOperationAudit(
            operation_type="reject_link",
            primary_person_id=person_id or (mention.person_id or 1),
            snapshot_details={
                "mention_id": mention.id,
                "raw_text": mention.raw_text,
                "rejected_person_id": person_id,
                "remaining_candidates": current_cands,
            },
            reason=reason or f"Rejected candidate person {person_id} for mention '{mention.raw_text}'",
            operator=reviewer,
        )
        db.add(audit_entry)
        db.commit()

        logger.info("Rejected resolution candidate: mention_id=%s rejected_person_id=%s", mention_id, person_id)
        return mention

    def defer_resolution(
        self,
        db: Session,
        mention_id: int,
        reviewer: str = "curator",
        reason: Optional[str] = None,
    ) -> PersonMention:
        """Mark mention resolution as uncertain or contested for further historiographical review."""
        mention = db.query(PersonMention).filter_by(id=mention_id).first()
        if not mention:
            raise AppException(f"PersonMention {mention_id} not found", code="MENTION_NOT_FOUND")

        mention.resolution_status = ResolutionStatus.UNCERTAIN
        mention.updated_at = datetime.utcnow()

        audit_entry = EntityOperationAudit(
            operation_type="defer_link",
            primary_person_id=mention.person_id or 1,
            snapshot_details={
                "mention_id": mention.id,
                "raw_text": mention.raw_text,
                "status": "deferred_uncertain",
            },
            reason=reason or "Deferred due to insufficient historical evidence or conflicting sources",
            operator=reviewer,
        )
        db.add(audit_entry)
        db.commit()

        return mention


disambiguation_service = DisambiguationService()
