"""Reversible entity merge, split, and audit trail management service."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger, AppException
from app.models.kg.audit import EntityOperationAudit
from app.models.kg.authority import ExternalAuthority
from app.models.kg.enums import IdentityStatus
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship


class EntityMergeService:
    """Manages transactional entity merges, conflict checks, and auditable reversible splits."""

    def preview_merge(
        self,
        db: Session,
        primary_person_id: int,
        secondary_person_id: int,
    ) -> Dict[str, Any]:
        """Preview the effects and detect potential conflicts before executing a merge."""
        if primary_person_id == secondary_person_id:
            raise AppException("Cannot merge a person entity into itself", code="INVALID_MERGE_TARGET")

        primary = db.query(Person).filter_by(id=primary_person_id).first()
        if not primary:
            raise AppException(f"Primary Person {primary_person_id} not found", code="PERSON_NOT_FOUND")

        secondary = db.query(Person).filter_by(id=secondary_person_id).first()
        if not secondary:
            raise AppException(f"Secondary Person {secondary_person_id} not found", code="PERSON_NOT_FOUND")

        # Conflict checks
        conflicts = []
        if primary.death_year and secondary.death_year and primary.death_year != secondary.death_year:
            conflicts.append(f"Death year mismatch: {primary.death_year} vs {secondary.death_year}")
        if primary.birth_year and secondary.birth_year and primary.birth_year != secondary.birth_year:
            conflicts.append(f"Birth year mismatch: {primary.birth_year} vs {secondary.birth_year}")

        mentions_count = db.query(PersonMention).filter_by(person_id=secondary.id).count()
        out_rels_count = db.query(Relationship).filter_by(source_person_id=secondary.id).count()
        in_rels_count = db.query(Relationship).filter_by(target_person_id=secondary.id).count()
        authorities_count = db.query(ExternalAuthority).filter_by(person_id=secondary.id).count()

        return {
            "primary_person": {
                "id": primary.id,
                "display_name": primary.display_name,
                "primary_role": primary.primary_role,
            },
            "secondary_person": {
                "id": secondary.id,
                "display_name": secondary.display_name,
                "primary_role": secondary.primary_role,
            },
            "impact": {
                "mentions_to_relink": mentions_count,
                "relationships_to_relink": out_rels_count + in_rels_count,
                "authorities_to_relink": authorities_count,
            },
            "potential_conflicts": conflicts,
            "can_merge": len(conflicts) == 0,
        }

    def execute_merge(
        self,
        db: Session,
        primary_person_id: int,
        secondary_person_id: int,
        operator: str = "curator",
        reason: Optional[str] = None,
    ) -> EntityOperationAudit:
        """
        Execute atomic entity merge with full snapshot persistence for reversible rollback.
        Redirects mentions, relationships, and authorities from secondary to primary person.
        """
        if primary_person_id == secondary_person_id:
            raise AppException("Cannot merge a person entity into itself", code="INVALID_MERGE_TARGET")

        primary = db.query(Person).filter_by(id=primary_person_id).first()
        secondary = db.query(Person).filter_by(id=secondary_person_id).first()
        if not primary or not secondary:
            raise AppException("One or both persons not found for merge", code="PERSON_NOT_FOUND")

        # 1. Capture exact snapshot of secondary person and items to be redirected
        sec_mentions = db.query(PersonMention).filter_by(person_id=secondary.id).all()
        sec_out_rels = db.query(Relationship).filter_by(source_person_id=secondary.id).all()
        sec_in_rels = db.query(Relationship).filter_by(target_person_id=secondary.id).all()
        sec_authorities = db.query(ExternalAuthority).filter_by(person_id=secondary.id).all()

        moved_mention_ids = [m.id for m in sec_mentions]
        moved_out_rel_ids = [r.id for r in sec_out_rels]
        moved_in_rel_ids = [r.id for r in sec_in_rels]
        moved_auth_ids = [a.id for a in sec_authorities]

        snapshot_details = {
            "secondary_person_id": secondary.id,
            "secondary_display_name": secondary.display_name,
            "secondary_identity_status": secondary.identity_status.value,
            "moved_mention_ids": moved_mention_ids,
            "moved_out_relationship_ids": moved_out_rel_ids,
            "moved_in_relationship_ids": moved_in_rel_ids,
            "moved_authority_ids": moved_auth_ids,
            "primary_previous_aliases": primary.aliases,
        }

        # 2. Redirect mentions
        for m in sec_mentions:
            m.person_id = primary.id
            m.updated_at = datetime.utcnow()

        # 3. Redirect relationships (prevent self-loops)
        for r in sec_out_rels:
            if r.target_person_id == primary.id:
                # Merge self-loop relationship: delete or keep evidence
                db.delete(r)
            else:
                r.source_person_id = primary.id

        for r in sec_in_rels:
            if r.source_person_id == primary.id:
                db.delete(r)
            else:
                r.target_person_id = primary.id

        # 4. Redirect external authorities
        for a in sec_authorities:
            a.person_id = primary.id
            a.updated_at = datetime.utcnow()

        # 5. Merge aliases into primary person
        existing_aliases = set()
        if primary.aliases:
            if isinstance(primary.aliases, list):
                existing_aliases.update(primary.aliases)
            elif isinstance(primary.aliases, dict):
                existing_aliases.update(primary.aliases.values())

        existing_aliases.add(secondary.display_name)
        if secondary.canonical_name_en:
            existing_aliases.add(secondary.canonical_name_en)
        if secondary.canonical_name_zh:
            existing_aliases.add(secondary.canonical_name_zh)
        if secondary.aliases:
            if isinstance(secondary.aliases, list):
                existing_aliases.update(secondary.aliases)
            elif isinstance(secondary.aliases, dict):
                existing_aliases.update(secondary.aliases.values())

        primary.aliases = list(existing_aliases)
        primary.updated_at = datetime.utcnow()

        # 6. Mark secondary person as merged/rejected
        secondary.identity_status = IdentityStatus.REJECTED
        secondary.biographical_notes = (
            f"Merged into Person ID {primary.id} on {datetime.utcnow().isoformat()}. "
            + (secondary.biographical_notes or "")
        )
        secondary.updated_at = datetime.utcnow()

        # 7. Record immutable audit entry
        audit_entry = EntityOperationAudit(
            operation_type="merge",
            primary_person_id=primary.id,
            secondary_person_id=secondary.id,
            snapshot_details=snapshot_details,
            reason=reason or f"Merged '{secondary.display_name}' into '{primary.display_name}'",
            operator=operator,
            is_reverted=False,
        )
        db.add(audit_entry)
        db.commit()

        logger.info(
            "Executed entity merge: primary=%s secondary=%s mentions=%d rels=%d (audit_id=%s)",
            primary.id, secondary.id, len(moved_mention_ids), len(moved_out_rel_ids) + len(moved_in_rel_ids), audit_entry.id
        )
        return audit_entry

    def split_or_rollback_merge(
        self,
        db: Session,
        audit_id: int,
        operator: str = "curator",
        reason: Optional[str] = None,
    ) -> EntityOperationAudit:
        """
        Reversibly rollback a previous merge operation using its audit snapshot.
        Restores secondary person and points mentions, relationships, and authorities back.
        """
        audit_entry = db.query(EntityOperationAudit).filter_by(id=audit_id).first()
        if not audit_entry:
            raise AppException(f"Audit log entry {audit_id} not found", code="AUDIT_NOT_FOUND")

        if audit_entry.operation_type != "merge":
            raise AppException("Only 'merge' operations can be reversed/split", code="INVALID_OPERATION_TYPE")

        if audit_entry.is_reverted:
            raise AppException(f"Operation {audit_id} has already been reverted", code="ALREADY_REVERTED")

        snapshot = audit_entry.snapshot_details
        secondary_id = snapshot.get("secondary_person_id")
        secondary = db.query(Person).filter_by(id=secondary_id).first()
        if not secondary:
            raise AppException(f"Secondary Person {secondary_id} not found for restore", code="PERSON_NOT_FOUND")

        primary = db.query(Person).filter_by(id=audit_entry.primary_person_id).first()

        # 1. Rollback mentions
        moved_mention_ids = snapshot.get("moved_mention_ids", [])
        if moved_mention_ids:
            mentions = db.query(PersonMention).filter(PersonMention.id.in_(moved_mention_ids)).all()
            for m in mentions:
                m.person_id = secondary.id
                m.updated_at = datetime.utcnow()

        # 2. Rollback relationships
        moved_out_rel_ids = snapshot.get("moved_out_relationship_ids", [])
        if moved_out_rel_ids:
            out_rels = db.query(Relationship).filter(Relationship.id.in_(moved_out_rel_ids)).all()
            for r in out_rels:
                r.source_person_id = secondary.id

        moved_in_rel_ids = snapshot.get("moved_in_relationship_ids", [])
        if moved_in_rel_ids:
            in_rels = db.query(Relationship).filter(Relationship.id.in_(moved_in_rel_ids)).all()
            for r in in_rels:
                r.target_person_id = secondary.id

        # 3. Rollback authorities
        moved_auth_ids = snapshot.get("moved_authority_ids", [])
        if moved_auth_ids:
            auths = db.query(ExternalAuthority).filter(ExternalAuthority.id.in_(moved_auth_ids)).all()
            for a in auths:
                a.person_id = secondary.id
                a.updated_at = datetime.utcnow()

        # 4. Restore secondary person status
        prev_status_str = snapshot.get("secondary_identity_status", "provisional")
        try:
            secondary.identity_status = IdentityStatus(prev_status_str)
        except ValueError:
            secondary.identity_status = IdentityStatus.PROVISIONAL

        secondary.updated_at = datetime.utcnow()

        # 5. Restore primary aliases if recorded
        if primary and "primary_previous_aliases" in snapshot:
            primary.aliases = snapshot["primary_previous_aliases"]
            primary.updated_at = datetime.utcnow()

        # 6. Mark original merge as reverted
        audit_entry.is_reverted = True

        # 7. Create split audit entry
        split_audit = EntityOperationAudit(
            operation_type="split",
            primary_person_id=audit_entry.primary_person_id,
            secondary_person_id=secondary.id,
            snapshot_details={
                "original_merge_audit_id": audit_id,
                "restored_mention_ids": moved_mention_ids,
                "restored_relationship_ids": moved_out_rel_ids + moved_in_rel_ids,
            },
            reason=reason or f"Reversed merge audit #{audit_id} and restored secondary Person {secondary.id}",
            operator=operator,
            is_reverted=False,
        )
        db.add(split_audit)
        db.commit()

        logger.info(
            "Reversed merge #%s: Restored person %s with %d mentions (split_audit_id=%s)",
            audit_id, secondary.id, len(moved_mention_ids), split_audit.id
        )
        return split_audit


entity_merge_service = EntityMergeService()
