"""Database persistence writer for extracted entities, mentions, and relationships."""
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core import logger
from app.models.kg import (
    Directionality,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    Person,
    PersonMention,
    RelationType,
    Relationship,
    RelationshipEvidence,
    ResolutionStatus,
    ReviewStatusKG,
    RoleSource,
)
from app.schemas.kg_extraction_schema import (
    ExtractedEntity,
    ExtractedRelationship,
    KGExtractionResult,
)
from app.services.kg import mention_linker
from app.services.kg.name_normalizer import name_normalizer


def _role_key(name: Optional[str]) -> str:
    """Comparison key used to pair a relationship endpoint with its extracted entity."""
    return name_normalizer.normalize_for_matching(name)


def _index_entity_roles(
    entities: Sequence[ExtractedEntity],
) -> Dict[str, Tuple[str, Optional[str]]]:
    """Map normalized entity name -> (role, organization) for this block only.

    Deliberately conservative:

    * the key is the normaliser's comparison key, never a similarity score;
    * when two entities share a key **with different roles**, the key is dropped instead
      of picking one (two people can share a name; guessing would attach the wrong job);
    * either value may be missing; an entry is only produced when a role or an
      organization was actually reported.
    """
    collected: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    for entity in entities:
        key = _role_key(entity.mention) or _role_key(entity.normalized_name)
        if not key:
            continue
        role = (entity.role or "").strip()
        organization = (entity.organization or "").strip()
        if not role and not organization:
            continue
        collected.setdefault(key, []).append((role, organization or None))

    index: Dict[str, Tuple[str, Optional[str]]] = {}
    for key, values in collected.items():
        roles = {role for role, _ in values if role}
        if len(roles) > 1:
            # Ambiguous within a single block: attach nothing rather than the wrong job.
            continue
        role = roles.pop() if roles else ""
        organizations = [org for _, org in values if org]
        index[key] = (role, organizations[0] if organizations else None)
    return index


class KGWriter:
    """Manages transactional, idempotent database writes of extracted mentions and relationships."""
    @staticmethod
    def _parse_iso_date(date_str: Optional[str]) -> Optional[date]:
        """Safely parse YYYY-MM-DD or YYYY string into date object."""
        if not date_str:
            return None
        s = date_str.strip()
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            try:
                # If only year given, default to Jan 1st of that year
                if len(s) == 4 and s.isdigit():
                    return date(int(s), 1, 1)
            except Exception:
                pass
        return None

    @staticmethod
    def _append_alias(person: Person, raw_name: str) -> None:
        """Record a variant spelling on the person that absorbed it.

        The archive keeps the wording the clerk used; the person keeps a canonical
        spelling. Storing the variant is what lets a reviewer see that
        ``Cap. Rogers`` and ``Capt. Rogers`` were folded together instead of guessing.
        """
        candidate = raw_name.strip()
        if not candidate:
            return
        existing = person.aliases
        if isinstance(existing, dict):
            values = [str(value) for value in existing.values()]
        elif isinstance(existing, (list, tuple)):
            values = [str(value) for value in existing]
        elif existing is None:
            values = []
        else:
            values = [str(existing)]
        lowered = candidate.lower()
        if any(value.strip().lower() == lowered for value in values):
            return
        if (person.display_name or "").strip().lower() == lowered:
            return
        if isinstance(existing, dict):
            merged = dict(existing)
            current = merged.get("variants")
            if current is not None and not isinstance(current, (list, tuple)):
                # Never reshape a value we do not understand: losing an existing alias
                # is worse than not recording this one.
                logger.warning(
                    "kg_writer: person #%s has a non-list aliases['variants']; leaving "
                    "aliases untouched instead of overwriting it",
                    person.id,
                )
                return
            merged["variants"] = [str(v) for v in (current or [])] + [candidate]
            person.aliases = merged
        else:
            person.aliases = values + [candidate]

    @staticmethod
    def _find_existing_person(
        db: Session,
        name_clean: str,
        index: Optional[Dict[str, List[Person]]] = None,
    ) -> Tuple[Optional[Person], List[Person]]:
        """Resolve a name to an existing person through the shared identity key.

        Returns ``(match, ambiguous_candidates)``. ``match`` is only ever a person the
        rules can point at *uniquely*:

        * the candidate that carries this exact spelling (reusing the record that
          already holds the wording is not an identity claim), or
        * the only candidate for the key.

        When several records share the key and none carries this spelling, ``match`` is
        ``None`` and the candidates are returned instead. Picking one of them would be a
        silent identity decision — e.g. ``Sir Edward Hughes`` and ``Mr. Edward Hughes``
        share a key but are not provably the same man, and nothing in the archive text
        says which one a new spelling means.
        """
        key = name_normalizer.identity_key(name_clean)
        if not key:
            return None, []
        if index is None:
            index = mention_linker.identity_index(db)
        # A merge performed by the curator while a block is being written marks the
        # absorbed record REJECTED; never attach new evidence to an identity that has
        # just been retired.
        candidates = [
            person
            for person in (index.get(key) or [])
            if person.identity_status != IdentityStatus.REJECTED
        ]
        if not candidates:
            return None, []
        lowered = name_clean.strip().lower()
        for person in candidates:  # an exact spelling always wins inside one key
            if (person.display_name or "").strip().lower() == lowered:
                return person, []
        if len(candidates) == 1:
            return candidates[0], []
        return None, candidates

    def _get_or_create_provisional_person(
        self,
        db: Session,
        display_name: str,
        role: Optional[str] = None,
        organization: Optional[str] = None,
        role_source: Optional[str] = None,
        index: Optional[Dict[str, List[Person]]] = None,
    ) -> Person:
        """Find an existing provisional Person matching display_name or create a new one.

        Role rules (see ``role_taxonomy``):

        * a newly created person receives the extracted role and its provenance;
        * an **existing non-empty role is never overwritten** — a later block must not
          silently rewrite who someone was;
        * an existing person with an empty role is filled in, because "no role recorded"
          is not a competing claim.

        Identity rules:

        * the lookup goes through ``name_normalizer.identity_key``, the same key the
          mention linker uses, so a spelling variant joins the existing person instead
          of adding a graph node, and the absorbed variant is kept in ``aliases``;
        * when several existing records share that key and none carries this exact
          spelling, nothing is merged and nothing is guessed: a **new provisional
          record** is created, carrying the spelling, the evidence and a note naming the
          conflicting ids. Deciding that two records are one person stays a human
          decision (``entity_merge_service``), never a side effect of extraction.
        """
        name_clean = display_name.strip()
        existing, ambiguous = self._find_existing_person(db, name_clean, index)
        if existing:
            if role and not (existing.primary_role or "").strip():
                existing.primary_role = role[:100]
                existing.role_source = role_source or RoleSource.MODEL_EXTRACTED.value
            if organization and not (existing.organization or "").strip():
                existing.organization = organization[:255]
            self._append_alias(existing, name_clean)
            return existing

        note = None
        if ambiguous:
            key = name_normalizer.identity_key(name_clean)
            note = (
                "Identity unresolved: identity key %r is shared by persons %s. This "
                "record was created for the spelling %r instead of attaching the "
                "occurrence to one of them; merge only after archival review."
                % (key, sorted(person.id for person in ambiguous), name_clean)
            )
            logger.warning(
                "kg_writer: identity key %r is shared by persons %s; created a new "
                "unresolved record for %r instead of guessing",
                key,
                sorted(person.id for person in ambiguous),
                name_clean,
            )

        new_person = Person(
            display_name=name_clean,
            canonical_name_en=name_clean,
            primary_role=(role[:100] if role else None),
            role_source=(role_source or RoleSource.MODEL_EXTRACTED.value) if role else None,
            organization=(organization[:255] if organization else None),
            biographical_notes=note,
            identity_status=IdentityStatus.PROVISIONAL,
            review_status=ReviewStatusKG.UNREVIEWED,
        )
        db.add(new_person)
        db.flush()
        if index is not None:
            # Keep the per-block index current: a later endpoint in the same block may
            # name this person with a different spelling.
            key = name_normalizer.identity_key(new_person.display_name)
            if key:
                index.setdefault(key, []).append(new_person)
        return new_person

    def write_extraction_result(
        self,
        db: Session,
        block_id: int,
        result: KGExtractionResult,
        overwrite_existing: bool = False,
        default_location: Optional[str] = None,
        default_date: Optional[date] = None,
        run_id: Optional[int] = None,
        source_kind: Optional[str] = None,
    ) -> Tuple[List[PersonMention], List[Relationship]]:
        """Persist extracted entities and relationships into PostgreSQL with deduplication."""
        # 1. Handle idempotency / overwrite
        if overwrite_existing:
            # Delete prior mentions and relationship evidences tied to this block
            db.query(RelationshipEvidence).filter_by(block_id=block_id).delete(synchronize_session=False)
            db.query(PersonMention).filter_by(block_id=block_id).delete(synchronize_session=False)
            db.flush()

        temporal_date = self._parse_iso_date(result.temporal_date_hint) or default_date
        spatial_location = (result.spatial_location_hint or default_location or "").strip() or None

        created_mentions: List[PersonMention] = []
        mention_map: Dict[str, PersonMention] = {}  # mention.lower() -> PersonMention

        # Exact-key index of the entities in *this* block, so a person created for a
        # relationship endpoint can inherit the role the model reported for that name.
        # Keyed by the normaliser's comparison key; names that normalise to the same key
        # but carry different roles are recorded as ambiguous and never guessed.
        entity_roles = _index_entity_roles(result.entities)

        # Identity index of the existing persons, rebuilt for every block: the graph can
        # be edited (merged) while an extraction run is still writing, so a cached index
        # would keep pointing at identities that no longer exist.
        person_index = mention_linker.identity_index(db)

        # 2. Write PersonMentions
        for ent in result.entities:
            raw_text = ent.mention.strip()
            if not raw_text:
                continue

            # Check if this mention already exists for this block
            existing_mention = (
                db.query(PersonMention)
                .filter_by(block_id=block_id, raw_text=raw_text)
                .first()
            )

            if existing_mention and not overwrite_existing:
                mention_map[raw_text.lower()] = existing_mention
                continue

            # Map mention_type
            m_type = MentionType.PERSON_NAME
            if ent.mention_type in ["title", "alias", "abbreviation"]:
                m_type = MentionType(ent.mention_type)

            role_text = (ent.role or "").strip()[:120] or None
            new_mention = PersonMention(
                block_id=block_id,
                person_id=None,  # identity linking is a separate, reviewable step
                raw_text=raw_text,
                normalized_text=(ent.normalized_name or raw_text).strip(),
                mention_type=m_type,
                temporal_date=temporal_date,
                temporal_year=temporal_date.year if temporal_date else None,
                spatial_location=spatial_location,
                confidence=float(ent.confidence),
                resolution_status=ResolutionStatus.UNRESOLVED,
                review_status=ReviewStatusKG.UNREVIEWED,
                evidence_text=ent.evidence,
                # The role wording belongs to this occurrence: keep it next to the block
                # and run that produced it so the role can always be traced back.
                role_text=role_text,
                role_source=(
                    RoleSource.MODEL_EXTRACTED.value if role_text else None
                ),
                extraction_run_id=run_id,
                source_kind=source_kind,
            )
            db.add(new_mention)
            db.flush()
            created_mentions.append(new_mention)
            mention_map[raw_text.lower()] = new_mention

        # 3. Write Relationships and RelationshipEvidences
        created_relationships: List[Relationship] = []

        for rel in result.relationships:
            src_text = rel.source_mention.strip()
            tgt_text = rel.target_mention.strip()
            if not src_text or not tgt_text or src_text.lower() == tgt_text.lower():
                continue

            # Get provisional persons for source and target, carrying the role the model
            # reported for that exact name in this block (no fuzzy matching: the lookup
            # is by the normaliser's comparison key and refuses ambiguous keys).
            src_role = entity_roles.get(_role_key(src_text))
            tgt_role = entity_roles.get(_role_key(tgt_text))
            source_person = self._get_or_create_provisional_person(
                db,
                src_text,
                role=src_role[0] if src_role else None,
                organization=src_role[1] if src_role else None,
                index=person_index,
            )
            target_person = self._get_or_create_provisional_person(
                db,
                tgt_text,
                role=tgt_role[0] if tgt_role else None,
                organization=tgt_role[1] if tgt_role else None,
                index=person_index,
            )

            # Map relation_type enum
            try:
                r_type = RelationType(rel.relation_type)
            except ValueError:
                r_type = RelationType.OTHER

            dir_type = Directionality.DIRECTED
            if rel.directionality == "undirected":
                dir_type = Directionality.UNDIRECTED

            # Check if relationship already exists
            existing_rel = (
                db.query(Relationship)
                .filter_by(
                    source_person_id=source_person.id,
                    target_person_id=target_person.id,
                    relation_type=r_type,
                )
                .first()
            )

            if existing_rel:
                relationship_obj = existing_rel
            else:
                relationship_obj = Relationship(
                    source_person_id=source_person.id,
                    target_person_id=target_person.id,
                    relation_type=r_type,
                    relation_label=rel.relation_label or r_type.value,
                    directionality=dir_type,
                    weight=rel.weight or 1.0,
                    confidence=float(rel.confidence),
                    spatial_location=spatial_location,
                    evidence_summary=rel.evidence,
                    review_status=ReviewStatusKG.UNREVIEWED,
                )
                db.add(relationship_obj)
                db.flush()
                created_relationships.append(relationship_obj)

            # Ground with RelationshipEvidence
            src_mention_obj = mention_map.get(src_text.lower())
            evidence_quote = rel.evidence or (src_mention_obj.evidence_text if src_mention_obj else None)

            # Check if this block already has this evidence link
            existing_evidence = (
                db.query(RelationshipEvidence)
                .filter_by(
                    relationship_id=relationship_obj.id,
                    block_id=block_id,
                )
                .first()
            )

            if not existing_evidence:
                evidence_obj = RelationshipEvidence(
                    relationship_id=relationship_obj.id,
                    person_mention_id=src_mention_obj.id if src_mention_obj else None,
                    block_id=block_id,
                    evidence_role=EvidenceRole.SUPPORTS,
                    quote_text=evidence_quote,
                    extraction_run_id=run_id,
                    source_kind=source_kind,
                )
                db.add(evidence_obj)
                db.flush()

        logger.info(
            "Wrote KG extraction to DB: block_id=%s new_mentions=%d new_relationships=%d",
            block_id, len(created_mentions), len(created_relationships)
        )
        # Linking is idempotent and only proposes candidate identities, so it can run
        # per block: a person created by a relationship immediately gains the archival
        # mentions that share its name instead of showing "0 mentions" forever.
        link_result = mention_linker.reconcile_block(db, block_id)
        logger.info(
            "Linked mentions for block %s: linked=%d ambiguous=%d no_person=%d",
            block_id,
            link_result["linked"],
            link_result["ambiguous"],
            link_result["no_person"],
        )
        return created_mentions, created_relationships


kg_writer = KGWriter()
