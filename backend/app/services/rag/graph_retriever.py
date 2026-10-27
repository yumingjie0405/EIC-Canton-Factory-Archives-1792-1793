"""Knowledge graph structured retrieval module for Graph RAG."""
from typing import Dict, List, Optional, Set, Tuple
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.block import Block
from app.models.kg.authority import ExternalAuthority
from app.models.kg.enums import ClaimStatus, IdentityStatus
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.schemas.rag import (
    RagEntityReference,
    RagRelationshipReference,
    RagSourceEvidence,
)
from app.services.rag.temporal_resolver import temporal_resolver


class GraphRetriever:
    """Retrieves structured historical entities, relationships, external authorities, and grounded evidence from KG."""

    def _in_scope(self, locator: Dict[str, object], group_document_ids: Optional[Set[int]]) -> bool:
        """True when the cited block's document is inside the research scope."""
        if group_document_ids is None:
            return True
        document_id = locator.get("document_id")
        if document_id is None:
            return False
        try:
            return int(document_id) in group_document_ids
        except (TypeError, ValueError):
            return False

    def _archival_locator(
        self,
        db: Session,
        block_id: Optional[int],
        cache: Dict[int, Dict[str, object]],
    ) -> Dict[str, object]:
        """Resolve the archive coordinates of a cited block.

        A graph citation is only usable if it can be traced back to the archival
        page it came from. Without the document id the UI cannot open the block
        context, and a researcher cannot verify the quote against the original,
        so the coordinates are loaded here instead of being left empty.
        """
        locator: Dict[str, object] = {
            "document_id": None,
            "page_number": None,
            "document_name": None,
            "temporal_coverage": None,
            "temporal_provenance": None,
        }
        if block_id is None:
            return locator
        if block_id in cache:
            return cache[block_id]

        block = (
            db.query(Block)
            .options(joinedload(Block.page).joinedload(Page.document))
            .filter(Block.id == block_id)
            .one_or_none()
        )
        if block is not None and block.page is not None:
            locator["page_number"] = block.page.page_number
            document = block.page.document
            if document is not None:
                locator["document_id"] = document.id
                locator["document_name"] = document.original_filename or document.filename
                info = temporal_resolver.resolve_document_temporal_info(
                    document.id, document.original_filename
                )
                locator["temporal_coverage"] = "%s [%s]" % (
                    info["temporal_label"],
                    info["provenance"],
                )
                locator["temporal_provenance"] = info["provenance"]

        cache[block_id] = locator
        return locator

    def retrieve_graph_context(
        self,
        db: Session,
        entity_names: List[str],
        time_filter: Optional[int] = None,
        group_document_ids: Optional[Set[int]] = None,
    ) -> Tuple[List[RagEntityReference], List[RagRelationshipReference], List[RagSourceEvidence]]:
        """Retrieve matched entities, relationship facts between them, and linked evidence.

        ``group_document_ids`` (server-side research scope): a knowledge-graph fact
        is only returned when the archival block that evidences it belongs to the
        group. Graph nodes/facts that exist globally but have no in-scope evidence
        are excluded — the group is never widened to the global graph.
        """
        if not entity_names:
            return [], [], []

        # 1. 查找匹配的人物实体
        matched_persons = self._find_persons_by_names(db, entity_names)
        if not matched_persons:
            return [], [], []

        person_ids = [p.id for p in matched_persons]

        # 2. 查询人物之间的直接关系以及关联的人物
        relationships, evidence_sources = self._find_relationships_and_evidences(
            db,
            person_ids,
            matched_persons,
            time_filter,
            group_document_ids=group_document_ids,
        )

        entity_refs: List[RagEntityReference] = [self._to_entity_ref(p) for p in matched_persons]
        if group_document_ids is not None:
            entity_refs = self._filter_entities_in_scope(
                db, entity_refs, matched_persons, relationships, group_document_ids
            )

        return entity_refs, relationships, evidence_sources

    def _filter_entities_in_scope(
        self,
        db: Session,
        entity_refs: List[RagEntityReference],
        matched_persons: List[Person],
        relationships: List[RagRelationshipReference],
        group_document_ids: Set[int],
    ) -> List[RagEntityReference]:
        """Keep only entities that are grounded in the group's own archival blocks."""
        from app.models.kg.mention import PersonMention

        grounded_names = set()
        for relationship in relationships:
            grounded_names.add(relationship.source_person_name)
            grounded_names.add(relationship.target_person_name)

        person_ids = [person.id for person in matched_persons]
        mentioned_ids = set()
        if person_ids:
            mentioned_ids = {
                row[0]
                for row in db.query(PersonMention.person_id)
                .join(Block, Block.id == PersonMention.block_id)
                .join(Page, Page.id == Block.page_id)
                .filter(
                    PersonMention.person_id.in_(person_ids),
                    Page.document_id.in_(set(group_document_ids)),
                )
                .all()
            }

        kept = []
        for person, ref in zip(matched_persons, entity_refs):
            if person.id in mentioned_ids or ref.display_name in grounded_names:
                kept.append(ref)
        return kept

    def get_entity_subgraph_context(
        self,
        db: Session,
        person_id: int,
        group_document_ids: Optional[Set[int]] = None,
    ) -> Tuple[Optional[RagEntityReference], List[RagEntityReference], List[RagRelationshipReference], List[RagSourceEvidence]]:
        """Retrieve full ego-network context for a single entity.

        When ``group_document_ids`` is provided, only relationships backed by an
        archival block inside those documents are returned.
        """
        person = db.query(Person).filter(Person.id == person_id).first()
        if not person:
            return None, [], [], []

        entity_ref = self._to_entity_ref(person)

        # 查找所有与此人物相关的关系
        rels = (
            db.query(Relationship)
            .filter(
                or_(
                    Relationship.source_person_id == person_id,
                    Relationship.target_person_id == person_id,
                )
            )
            .all()
        )

        neighbor_ids = set()
        for r in rels:
            if r.source_person_id != person_id:
                neighbor_ids.add(r.source_person_id)
            if r.target_person_id != person_id:
                neighbor_ids.add(r.target_person_id)

        neighbor_persons = db.query(Person).filter(Person.id.in_(neighbor_ids)).all() if neighbor_ids else []
        neighbor_refs = [self._to_entity_ref(p) for p in neighbor_persons]

        all_persons_map = {p.id: p for p in [person] + neighbor_persons}

        rel_refs: List[RagRelationshipReference] = []
        ev_sources: List[RagSourceEvidence] = []
        ev_count = 1
        locator_cache: Dict[int, Dict[str, object]] = {}

        for r in rels:
            s_name = all_persons_map.get(r.source_person_id, None)
            t_name = all_persons_map.get(r.target_person_id, None)
            s_label = s_name.display_name if s_name else f"Person #{r.source_person_id}"
            t_label = t_name.display_name if t_name else f"Person #{r.target_person_id}"

            evidence_quote = None
            kept_evidence = 0
            if r.evidences:
                for ev in r.evidences:
                    locator = self._archival_locator(db, ev.block_id, locator_cache)
                    if not self._in_scope(locator, group_document_ids):
                        continue
                    kept_evidence += 1
                    if evidence_quote is None:
                        evidence_quote = ev.quote_text
                    ev_sources.append(
                        RagSourceEvidence(
                            source_id=f"图谱引用 #{ev_count}",
                            document_id=locator["document_id"],
                            document_name=locator["document_name"] or "东印度公司档案记录",
                            page_number=locator["page_number"],
                            block_id=ev.block_id,
                            quote_text=ev.quote_text or "档案原始关系记录",
                            relevance_score=0.95,
                            matched_entities=[s_label, t_label],
                            temporal_coverage=locator["temporal_coverage"],
                            temporal_provenance=locator["temporal_provenance"],
                        )
                    )
                    ev_count += 1

            if group_document_ids is not None and kept_evidence == 0:
                continue

            rel_refs.append(
                RagRelationshipReference(
                    source_person_name=s_label,
                    target_person_name=t_label,
                    relation_type=r.relation_type.value if hasattr(r.relation_type, "value") else str(r.relation_type),
                    time_description=r.time_description,
                    confidence=r.confidence,
                    claim_status=r.claim_status.value if hasattr(r.claim_status, "value") else str(r.claim_status),
                    review_status=r.review_status.value if hasattr(r.review_status, "value") else str(r.review_status),
                    evidence_quote=evidence_quote,
                )
            )

        return entity_ref, neighbor_refs, rel_refs, ev_sources

    def _find_persons_by_names(self, db: Session, entity_names: List[str]) -> List[Person]:
        """Find person records matching any given name variants."""
        matched: List[Person] = []
        seen_ids: Set[int] = set()

        for name in entity_names:
            term = f"%{name.strip()}%"
            results = (
                db.query(Person)
                .filter(
                    or_(
                        Person.display_name.ilike(term),
                        Person.canonical_name_zh.ilike(term),
                        Person.canonical_name_en.ilike(term),
                    )
                )
                .all()
            )
            for p in results:
                if p.id not in seen_ids:
                    matched.append(p)
                    seen_ids.add(p.id)

        return matched

    def _find_relationships_and_evidences(
        self,
        db: Session,
        person_ids: List[int],
        matched_persons: List[Person],
        time_filter: Optional[int],
        group_document_ids: Optional[Set[int]] = None,
    ) -> Tuple[List[RagRelationshipReference], List[RagSourceEvidence]]:
        """Query relationships touching the matched entities and extract grounded evidences.

        With ``group_document_ids`` set, only evidences whose archival block belongs
        to those documents are kept, and a relationship without any in-scope
        evidence is dropped entirely.
        """
        person_map = {p.id: p for p in matched_persons}

        # 如果有多个实体，优先查询两两之间的关系；如果只有1个实体，查询该实体的1度对外关系
        query = db.query(Relationship)
        if len(person_ids) >= 2:
            query = query.filter(
                or_(
                    Relationship.source_person_id.in_(person_ids),
                    Relationship.target_person_id.in_(person_ids),
                )
            )
        else:
            query = query.filter(
                or_(
                    Relationship.source_person_id.in_(person_ids),
                    Relationship.target_person_id.in_(person_ids),
                )
            )

        rels = query.limit(10).all()

        # 补全缺失的关系对象姓名
        missing_ids = set()
        for r in rels:
            if r.source_person_id not in person_map:
                missing_ids.add(r.source_person_id)
            if r.target_person_id not in person_map:
                missing_ids.add(r.target_person_id)

        if missing_ids:
            other_persons = db.query(Person).filter(Person.id.in_(missing_ids)).all()
            for op in other_persons:
                person_map[op.id] = op

        rel_refs: List[RagRelationshipReference] = []
        ev_sources: List[RagSourceEvidence] = []
        ev_idx = 1
        locator_cache: Dict[int, Dict[str, object]] = {}

        for r in rels:
            s_p = person_map.get(r.source_person_id)
            t_p = person_map.get(r.target_person_id)
            s_name = s_p.display_name if s_p else f"Person #{r.source_person_id}"
            t_name = t_p.display_name if t_p else f"Person #{r.target_person_id}"

            quote = None
            kept_evidence = 0
            if r.evidences:
                for ev in r.evidences:
                    if ev.quote_text:
                        locator = self._archival_locator(db, ev.block_id, locator_cache)
                        if not self._in_scope(locator, group_document_ids):
                            continue
                        kept_evidence += 1
                        quote = ev.quote_text
                        ev_sources.append(
                            RagSourceEvidence(
                                source_id=f"关系引证 #{ev_idx}",
                                document_id=locator["document_id"],
                                document_name=locator["document_name"]
                                or "广州商馆咨询会议记录 (Canton Consultations)",
                                page_number=locator["page_number"],
                                block_id=ev.block_id,
                                quote_text=ev.quote_text,
                                relevance_score=0.92,
                                matched_entities=[s_name, t_name],
                                temporal_coverage=locator["temporal_coverage"],
                                temporal_provenance=locator["temporal_provenance"],
                            )
                        )
                        ev_idx += 1

            if group_document_ids is not None and kept_evidence == 0:
                # The fact is not evidenced inside the group: it must not appear as
                # group research output.
                continue

            rel_refs.append(
                RagRelationshipReference(
                    source_person_name=s_name,
                    target_person_name=t_name,
                    relation_type=r.relation_type.value if hasattr(r.relation_type, "value") else str(r.relation_type),
                    time_description=r.time_description,
                    confidence=r.confidence,
                    claim_status=r.claim_status.value if hasattr(r.claim_status, "value") else str(r.claim_status),
                    review_status=r.review_status.value if hasattr(r.review_status, "value") else str(r.review_status),
                    evidence_quote=quote,
                )
            )

        return rel_refs, ev_sources

    def _to_entity_ref(self, person: Person) -> RagEntityReference:
        """Convert a Person ORM object to RagEntityReference schema."""
        external_records = []
        authorities = getattr(person, "external_authorities", None) or getattr(person, "authorities", [])
        for auth in authorities:
            rec = f"{auth.authority_source.value if hasattr(auth.authority_source, 'value') else auth.authority_source}: {auth.record_title or auth.external_id or '考证记录'}"
            if auth.historical_notes:
                rec += f" ({auth.historical_notes})"
            external_records.append(rec)

        return RagEntityReference(
            id=person.id,
            display_name=person.display_name,
            primary_role=person.primary_role,
            canonical_name_zh=person.canonical_name_zh,
            canonical_name_en=person.canonical_name_en,
            organization=person.organization,
            identity_status=person.identity_status.value if hasattr(person.identity_status, "value") else str(person.identity_status),
            external_authorities_count=len(external_records),
            external_records=external_records,
        )


graph_retriever = GraphRetriever()
