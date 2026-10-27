"""PostgreSQL tests: group-scoped person graph (evidence-first isolation).

What is proven here:

* a group graph contains only persons/relationships supported by **that group's own**
  blocks (an accepted relationship whose evidence lives in another group never leaks);
* a shared global identity appears in both groups without importing foreign edges;
* review status and claim status filter the official view, and candidates stay in the
  explicit candidate view;
* SNA metrics are computed on the scoped subgraph (not the global one);
* unknown group ⇒ 404, empty group / no-text / not-extracted states are distinguished;
* evidence endpoints return per-source rows of the current group only.

Only the database is exercised; no LLM call is made.
"""
import unittest

from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.api.routers import kg as kg_router
from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.kg.enums import (
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.kg.group_graph import load_scope
from app.services.kg.sna_service import sna_service
from app.schemas.kg_sna import SNAGraphFilterParams
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-KGG]"
GROUP_A = SYNTH + " Group A"
GROUP_B = SYNTH + " Group B"
GROUP_EMPTY = SYNTH + " Empty group"
GROUP_NO_TEXT = SYNTH + " Group without text"


def _pdf_bytes(marker: str) -> bytes:
    return ("%PDF-1.4\n% " + marker + "\n%%EOF\n").encode("utf-8")


@requires_postgres_test_db
class TestKgGroupGraphPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()

        cls.doc_a = cls._document("Synthetic_KGG_groupA_1792.pdf", "A")
        cls.doc_b = cls._document("Synthetic_KGG_groupB_1793.pdf", "B")
        cls.doc_no_text = cls._document("Synthetic_KGG_notext.pdf", None)

        cls.group_a = cls.service.create_group(GroupCreate(name=GROUP_A, description="scope A"))
        cls.group_b = cls.service.create_group(GroupCreate(name=GROUP_B, description="scope B"))
        cls.group_empty = cls.service.create_group(GroupCreate(name=GROUP_EMPTY))
        cls.group_no_text = cls.service.create_group(GroupCreate(name=GROUP_NO_TEXT))

        for group, doc in (
            (cls.group_a, cls.doc_a),
            (cls.group_b, cls.doc_b),
            (cls.group_no_text, cls.doc_no_text),
        ):
            cls.db.add(
                ResearchGroupDocument(
                    group_id=group.id,
                    document_id=doc["document_id"],
                    import_status=GroupImportStatus.READY,
                    last_batch_id="kgg",
                )
            )
        cls.db.commit()

        cls.browne = cls._person(SYNTH + " Henry Browne", IdentityStatus.ACCEPTED)
        cls.howqua = cls._person(SYNTH + " Howqua", IdentityStatus.ACCEPTED)
        cls.puankhequa = cls._person(SYNTH + " Puankhequa", IdentityStatus.ACCEPTED)
        cls.provisional = cls._person(SYNTH + " Anonymous Supercargo", IdentityStatus.PROVISIONAL)

        # Mentions: Browne + Howqua + provisional in group A; Puankhequa in group B.
        cls._mention(cls.doc_a["block_id"], cls.browne.id)
        cls._mention(cls.doc_a["block_id"], cls.howqua.id)
        cls._mention(cls.doc_a["block_id"], cls.provisional.id)
        cls._mention(cls.doc_b["block_id"], cls.puankhequa.id)
        cls._mention(cls.doc_b["block_id"], cls.browne.id)

        # R_accepted_a: Browne–Howqua, accepted, evidence only in group A.
        cls.rel_a = cls._relationship(
            cls.browne.id, cls.howqua.id, ClaimStatus.ACCEPTED, ReviewStatusKG.REVIEWED
        )
        cls._evidence(cls.rel_a.id, cls.doc_a["block_id"], "woollen contract in A")

        # R_leak: Browne–Howqua again, accepted, but evidence ONLY in group B.
        # Both endpoints are in group A's person set, so endpoint-based filtering
        # alone would wrongly show it in A.
        cls.rel_leak = cls._relationship(
            cls.browne.id, cls.howqua.id, ClaimStatus.ACCEPTED, ReviewStatusKG.REVIEWED
        )
        cls._evidence(cls.rel_leak.id, cls.doc_b["block_id"], "tea shipment in B")

        # R_b_only: Howqua–Puankhequa, accepted, evidence in group B.
        cls.rel_b = cls._relationship(
            cls.howqua.id, cls.puankhequa.id, ClaimStatus.ACCEPTED, ReviewStatusKG.REVIEWED
        )
        cls._evidence(cls.rel_b.id, cls.doc_b["block_id"], "security merchant bond in B")

        # R_rejected: rejected claim, evidence in group A.
        cls.rel_rejected = cls._relationship(
            cls.browne.id, cls.puankhequa.id, ClaimStatus.REJECTED, ReviewStatusKG.REVIEWED
        )
        cls._evidence(cls.rel_rejected.id, cls.doc_a["block_id"], "refuted claim in A")

        # R_candidate: asserted (not yet accepted), evidence in group A.
        cls.rel_candidate = cls._relationship(
            cls.howqua.id, cls.provisional.id, ClaimStatus.ASSERTED, ReviewStatusKG.UNREVIEWED
        )
        cls._evidence(cls.rel_candidate.id, cls.doc_a["block_id"], "draft claim in A")

        cls.db.commit()

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _document(cls, filename, marker):
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1 if marker else 0,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(document)
        cls.db.flush()
        if marker is None:
            cls.db.commit()
            return {"document_id": document.id, "page_id": None, "block_id": None}
        page = Page(document_id=document.id, page_number=1, width=1000, height=1400)
        cls.db.add(page)
        cls.db.flush()
        block = Block(
            page_id=page.id,
            block_index=0,
            block_label="paragraph",
            bbox=[0, 0, 10, 10],
            ocr_text_raw=SYNTH + " archival text for group " + marker,
        )
        cls.db.add(block)
        cls.db.commit()
        return {"document_id": document.id, "page_id": page.id, "block_id": block.id}

    @classmethod
    def _document_with_blocks(cls, filename, marker, count):
        """A document whose single page holds ``count`` blocks (high-volume fixtures)."""
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(document)
        cls.db.flush()
        page = Page(document_id=document.id, page_number=1, width=1000, height=1400)
        cls.db.add(page)
        cls.db.flush()
        block_ids = []
        for index in range(count):
            block = Block(
                page_id=page.id,
                block_index=index,
                block_label="paragraph",
                bbox=[0, 0, 10, 10],
                ocr_text_raw="%s archival text %d for group %s" % (SYNTH, index, marker),
            )
            cls.db.add(block)
            cls.db.flush()
            block_ids.append(block.id)
        cls.db.commit()
        return {
            "document_id": document.id,
            "page_id": page.id,
            "block_id": block_ids[0] if block_ids else None,
            "block_ids": block_ids,
        }

    @classmethod
    def _person(cls, name, status_value):
        person = Person(
            display_name=name,
            canonical_name_en=name,
            primary_role="Supercargo",
            identity_status=status_value,
        )
        cls.db.add(person)
        cls.db.commit()
        return person

    @classmethod
    def _mention(cls, block_id, person_id):
        mention = PersonMention(
            block_id=block_id,
            person_id=person_id,
            raw_text=SYNTH + " mention",
            mention_type=MentionType.PERSON_NAME,
            resolution_status=ResolutionStatus.RESOLVED,
            review_status=ReviewStatusKG.REVIEWED,
        )
        cls.db.add(mention)
        cls.db.commit()
        return mention

    @classmethod
    def _relationship(cls, source_id, target_id, claim, review):
        relationship = Relationship(
            source_person_id=source_id,
            target_person_id=target_id,
            relation_type=RelationType.COMMERCIAL,
            claim_status=claim,
            review_status=review,
            confidence=0.9,
            evidence_summary=SYNTH + " synthetic relation",
        )
        cls.db.add(relationship)
        cls.db.commit()
        return relationship

    @classmethod
    def _evidence(cls, relationship_id, block_id, quote):
        cls.db.add(
            RelationshipEvidence(
                relationship_id=relationship_id,
                block_id=block_id,
                quote_text=SYNTH + " " + quote,
                evidence_role=EvidenceRole.SUPPORTS,
            )
        )
        cls.db.commit()

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            group_ids = [
                row[0]
                for row in db.query(ResearchGroup.id)
                .filter(
                    ResearchGroup.name.in_([GROUP_A, GROUP_B, GROUP_EMPTY, GROUP_NO_TEXT])
                )
                .all()
            ]
            if group_ids:
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(group_ids)).delete(
                    synchronize_session=False
                )
            person_ids = [
                row[0]
                for row in db.query(Person.id)
                .filter(Person.display_name.like(SYNTH + "%"))
                .all()
            ]
            if person_ids:
                relationship_ids = [
                    row[0]
                    for row in db.query(Relationship.id)
                    .filter(
                        Relationship.source_person_id.in_(person_ids)
                        | Relationship.target_person_id.in_(person_ids)
                    )
                    .all()
                ]
                if relationship_ids:
                    db.query(RelationshipEvidence).filter(
                        RelationshipEvidence.relationship_id.in_(relationship_ids)
                    ).delete(synchronize_session=False)
                    db.query(Relationship).filter(
                        Relationship.id.in_(relationship_ids)
                    ).delete(synchronize_session=False)
                db.query(PersonMention).filter(
                    PersonMention.person_id.in_(person_ids)
                ).delete(synchronize_session=False)
                db.query(Person).filter(Person.id.in_(person_ids)).delete(
                    synchronize_session=False
                )
            doc_ids = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_KGG_%"))
                .all()
            ]
            if doc_ids:
                page_ids = [
                    row[0]
                    for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
                ]
                if page_ids:
                    db.query(Block).filter(Block.page_id.in_(page_ids)).delete(
                        synchronize_session=False
                    )
                    db.query(Page).filter(Page.id.in_(page_ids)).delete(
                        synchronize_session=False
                    )
                db.query(Document).filter(Document.id.in_(doc_ids)).delete(
                    synchronize_session=False
                )
            db.commit()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures
            db.rollback()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        app.dependency_overrides.pop(get_db, None)
        cls.db.close()
        cls.engine.dispose()

    # --------------------------------------------------------------- scope
    def test_01_scope_contains_only_group_evidence(self):
        scope_a = load_scope(self.db, self.group_a.id)
        self.assertEqual(scope_a.document_ids, (self.doc_a["document_id"],))
        self.assertIn(self.rel_a.id, scope_a.relationship_ids)
        self.assertIn(self.rel_candidate.id, scope_a.relationship_ids)
        self.assertIn(self.rel_rejected.id, scope_a.relationship_ids)
        # both endpoints live in A's person set, but the evidence is in B
        self.assertNotIn(self.rel_leak.id, scope_a.relationship_ids)
        self.assertNotIn(self.rel_b.id, scope_a.relationship_ids)

        scope_b = load_scope(self.db, self.group_b.id)
        self.assertIn(self.rel_leak.id, scope_b.relationship_ids)
        self.assertIn(self.rel_b.id, scope_b.relationship_ids)
        self.assertNotIn(self.rel_a.id, scope_b.relationship_ids)

    def test_02_unknown_group_is_404_everywhere(self):
        for path in (
            "/api/kg/groups/999999/graph/scope",
            "/api/kg/groups/999999/graph/overview",
            "/api/kg/persons?group_id=999999",
            "/api/kg/relationships?group_id=999999",
            "/api/kg/sna/summary?group_id=999999",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, "%s -> %s" % (path, response.text))

    # ------------------------------------------------------------ overview
    def test_03_official_overview_excludes_foreign_evidence(self):
        body = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=official" % self.group_a.id
        ).json()
        person_ids = {person["id"] for person in body["persons"]}
        relationship_ids = {rel["id"] for rel in body["relationships"]}

        self.assertEqual(body["group_name"], GROUP_A)
        self.assertEqual(person_ids, {self.browne.id, self.howqua.id})
        self.assertNotIn(self.puankhequa.id, person_ids)
        self.assertNotIn(self.provisional.id, person_ids)
        self.assertEqual(relationship_ids, {self.rel_a.id})
        self.assertNotIn(self.rel_leak.id, relationship_ids)
        self.assertNotIn(self.rel_rejected.id, relationship_ids)
        self.assertNotIn(self.rel_candidate.id, relationship_ids)

        edge = body["relationships"][0]
        self.assertEqual(edge["evidence_count"], 1)
        self.assertEqual(edge["evidence_block_ids"], [self.doc_a["block_id"]])

    def test_04_candidate_view_is_separate_and_labelled(self):
        body = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=candidates&include_provisional=true"
            % self.group_a.id
        ).json()
        relationship_ids = {rel["id"] for rel in body["relationships"]}
        self.assertEqual(body["view"], "candidates")
        self.assertIn(self.rel_candidate.id, relationship_ids)
        self.assertNotIn(self.rel_a.id, relationship_ids)
        self.assertNotIn(self.rel_rejected.id, relationship_ids)
        candidate = [rel for rel in body["relationships"] if rel["id"] == self.rel_candidate.id][0]
        self.assertEqual(candidate["claim_status"], "asserted")
        self.assertEqual(candidate["review_status"], "unreviewed")

        # The candidate edge points at a provisional person; identity filtering is
        # independent from claim review, so it only appears when provisional persons
        # are explicitly included.
        without_provisional = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=candidates&include_provisional=false"
            % self.group_a.id
        ).json()
        self.assertNotIn(
            self.rel_candidate.id, {rel["id"] for rel in without_provisional["relationships"]}
        )

        with_provisional = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=candidates&include_provisional=true"
            % self.group_a.id
        ).json()
        self.assertIn(
            self.rel_candidate.id, {rel["id"] for rel in with_provisional["relationships"]}
        )
        self.assertIn(
            self.provisional.id, {person["id"] for person in with_provisional["persons"]}
        )
        # … and asking for provisional persons does not turn the official edge on.
        self.assertNotIn(
            self.rel_a.id, {rel["id"] for rel in with_provisional["relationships"]}
        )

    def test_05_shared_identity_does_not_import_foreign_edges(self):
        body_a = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=official" % self.group_a.id
        ).json()
        body_b = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=official" % self.group_b.id
        ).json()

        # Browne appears in both groups (global identity shared) …
        self.assertIn(self.browne.id, {person["id"] for person in body_a["persons"]})
        self.assertIn(self.browne.id, {person["id"] for person in body_b["persons"]})
        # … but neither group shows the other group's edges.
        self.assertNotIn(self.rel_leak.id, {rel["id"] for rel in body_a["relationships"]})
        self.assertNotIn(self.rel_a.id, {rel["id"] for rel in body_b["relationships"]})

    # ----------------------------------------------------------------- SNA
    def test_06_sna_metrics_are_computed_on_the_subgraph(self):
        # A group defaults to view=all: everything extracted from this group's own
        # evidence (provisional persons included) is visible with its sources.
        scoped = self.client.get(
            "/api/kg/sna/summary?group_id=%d" % self.group_a.id
        ).json()
        self.assertEqual(scoped["group_id"], self.group_a.id)
        self.assertEqual(scoped["group_name"], GROUP_A)
        self.assertEqual(scoped["view"], "all")
        self.assertEqual(scoped["node_count"], 3)
        self.assertEqual(scoped["edge_count"], 2)
        self.assertEqual(scoped["scope_document_count"], 1)
        self.assertEqual(scoped["filter_parameters"]["group_id"], self.group_a.id)

        # The official view stays the strict, review-gated subgraph.
        official = self.client.get(
            "/api/kg/sna/summary?group_id=%d&view=official" % self.group_a.id
        ).json()
        self.assertEqual(official["view"], "official")
        self.assertEqual(official["node_count"], 2)
        self.assertEqual(official["edge_count"], 1)
        self.assertAlmostEqual(official["density"], 0.5, places=6)

        scoped_b = self.client.get(
            "/api/kg/sna/summary?group_id=%d&view=official" % self.group_b.id
        ).json()
        self.assertEqual(scoped_b["node_count"], 3)
        self.assertEqual(scoped_b["edge_count"], 2)

        global_summary = sna_service.get_network_summary(
            self.db, SNAGraphFilterParams(include_provisional=False)
        )
        self.assertGreater(global_summary.edge_count, official["edge_count"])
        self.assertNotEqual(global_summary.density, official["density"])

    def test_07_person_metrics_respect_the_group(self):
        outside = self.client.get(
            "/api/kg/sna/persons/%d/metrics?group_id=%d" % (self.puankhequa.id, self.group_a.id)
        ).json()
        self.assertFalse(outside["in_network"])
        self.assertIn("excluded", outside["note"])

        inside = self.client.get(
            "/api/kg/sna/persons/%d/metrics?group_id=%d" % (self.browne.id, self.group_a.id)
        ).json()
        self.assertTrue(inside["in_network"])
        self.assertEqual(inside["degree"], 1)

    # ------------------------------------------------------------ states
    def test_08_group_states_are_distinguished(self):
        ready = self.client.get("/api/kg/groups/%d/graph/scope" % self.group_a.id).json()
        self.assertEqual(ready["extraction_state"], "ready")
        # The extraction ledger exists now, so progress/pause reasons can be reported.
        self.assertTrue(ready["task_tracking_available"])
        self.assertIsNone(ready["extraction_run"])
        self.assertEqual(ready["document_count"], 1)
        self.assertEqual(ready["block_count"], 1)
        self.assertGreaterEqual(ready["mention_count"], 3)
        self.assertGreaterEqual(ready["relationship_count_official"], 1)

        empty = self.client.get("/api/kg/groups/%d/graph/scope" % self.group_empty.id).json()
        self.assertEqual(empty["extraction_state"], "no_documents")
        self.assertEqual(empty["relationship_count_official"], 0)
        self.assertTrue(empty["state_message"])

        no_text = self.client.get("/api/kg/groups/%d/graph/scope" % self.group_no_text.id).json()
        self.assertEqual(no_text["extraction_state"], "no_text")

    def test_09_empty_group_returns_empty_graph_never_global(self):
        overview = self.client.get(
            "/api/kg/groups/%d/graph/overview" % self.group_empty.id
        ).json()
        self.assertEqual(overview["persons"], [])
        self.assertEqual(overview["relationships"], [])

        summary = self.client.get(
            "/api/kg/sna/summary?group_id=%d" % self.group_empty.id
        ).json()
        self.assertEqual(summary["node_count"], 0)
        self.assertEqual(summary["edge_count"], 0)
        self.assertEqual(summary["group_id"], self.group_empty.id)

    def test_10_not_extracted_group_reports_no_mentions(self):
        document = self._document("Synthetic_KGG_unextracted.pdf", "U")
        group = self.service.create_group(GroupCreate(name=SYNTH + " Unextracted"))
        self.db.add(
            ResearchGroupDocument(
                group_id=group.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgg",
            )
        )
        self.db.commit()
        try:
            scope = self.client.get("/api/kg/groups/%d/graph/scope" % group.id).json()
            self.assertEqual(scope["extraction_state"], "not_extracted")
            self.assertEqual(scope["blocks_with_mentions"], 0)
            overview = self.client.get("/api/kg/groups/%d/graph/overview" % group.id).json()
            self.assertEqual(overview["persons"], [])
            # global persons are still reachable through the unscoped endpoint
            global_persons = self.client.get("/api/kg/persons?limit=200").json()
            self.assertGreater(global_persons["total"], len(overview["persons"]))
        finally:
            self.db.query(ResearchGroupDocument).filter(
                ResearchGroupDocument.group_id == group.id
            ).delete(synchronize_session=False)
            self.db.query(ResearchGroup).filter(ResearchGroup.id == group.id).delete(
                synchronize_session=False
            )
            self.db.commit()

    # ---------------------------------------------------------- evidence
    def test_11_relationship_evidence_is_per_source_and_scoped(self):
        body = self.client.get(
            "/api/kg/groups/%d/relationships/%d/evidence" % (self.group_a.id, self.rel_a.id)
        ).json()
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["document_id"], self.doc_a["document_id"])
        self.assertEqual(item["page_number"], 1)
        self.assertEqual(item["block_id"], self.doc_a["block_id"])
        self.assertIn("woollen contract in A", item["quote_text"])
        self.assertEqual(item["evidence_role"], "supports")
        self.assertEqual(body["out_of_group_evidence_count"], 0)

        foreign = self.client.get(
            "/api/kg/groups/%d/relationships/%d/evidence" % (self.group_a.id, self.rel_leak.id)
        ).json()
        self.assertEqual(foreign["items"], [])
        self.assertGreaterEqual(foreign["out_of_group_evidence_count"], 1)

    def test_11a_evidence_endpoint_is_bounded_for_high_volume_claims(self):
        """A claim with many evidence rows must not materialise every joined entity.

        Regression for the live "Failed to fetch": the endpoint joined full
        ``documents``/``pages`` entities per evidence row, and a mock run with 14,620
        rows made psycopg2 raise MemoryError (HTTP 500). Now only the needed columns are
        selected, the payload is capped, and the true totals are reported.
        """
        from app.services.kg import group_graph

        bulk = self._document_with_blocks("Synthetic_KGG_bulk.pdf", "B", 40)
        group = self.service.create_group(GroupCreate(name=SYNTH + " Bulk evidence"))
        self.db.add(
            ResearchGroupDocument(
                group_id=group.id,
                document_id=bulk["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgg",
            )
        )
        self.db.commit()
        relationship = self._relationship(
            self.browne.id, self.howqua.id, ClaimStatus.ASSERTED, ReviewStatusKG.UNREVIEWED
        )
        for block_id in bulk["block_ids"]:
            self._evidence(relationship.id, block_id, "bulk quote")

        body = self.client.get(
            "/api/kg/groups/%d/relationships/%d/evidence" % (group.id, relationship.id)
        ).json()
        self.assertEqual(body["in_group_evidence_count"], 40)
        self.assertEqual(len(body["items"]), 40)
        self.assertFalse(body["truncated"])
        self.assertEqual(body["out_of_group_evidence_count"], 0)

        capped = self.client.get(
            "/api/kg/groups/%d/relationships/%d/evidence?limit=5" % (group.id, relationship.id)
        ).json()
        self.assertEqual(len(capped["items"]), 5)
        self.assertTrue(capped["truncated"])
        self.assertEqual(capped["in_group_evidence_count"], 40)
        # The rows are the *first* evidence ids, not an arbitrary subset.
        self.assertEqual(
            [item["evidence_id"] for item in capped["items"]],
            sorted(item["evidence_id"] for item in capped["items"]),
        )

        # The clamp protects the database even when a caller asks for everything.
        self.assertEqual(group_graph.clamp_trace_limit(None), group_graph.TRACE_ROW_LIMIT)
        self.assertEqual(group_graph.clamp_trace_limit(10 ** 6), group_graph.TRACE_ROW_LIMIT_MAX)
        self.assertEqual(group_graph.clamp_trace_limit(0), group_graph.TRACE_ROW_LIMIT)
        self.assertEqual(group_graph.clamp_trace_limit(-3), group_graph.TRACE_ROW_LIMIT)

    def test_11b_mention_endpoint_is_bounded_and_reports_totals(self):
        bulk = self._document_with_blocks("Synthetic_KGG_bulk_mentions.pdf", "M", 25)
        group = self.service.create_group(GroupCreate(name=SYNTH + " Bulk mentions"))
        self.db.add(
            ResearchGroupDocument(
                group_id=group.id,
                document_id=bulk["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgg",
            )
        )
        self.db.commit()
        for block_id in bulk["block_ids"]:
            self.db.add(
                PersonMention(
                    block_id=block_id,
                    person_id=self.browne.id,
                    raw_text=SYNTH + " Browne",
                    normalized_text="henry browne",
                    mention_type=MentionType.PERSON_NAME,
                    resolution_status=ResolutionStatus.RESOLVED,
                    review_status=ReviewStatusKG.UNREVIEWED,
                )
            )
        self.db.commit()

        body = self.client.get(
            "/api/kg/groups/%d/persons/%d/mentions" % (group.id, self.browne.id)
        ).json()
        self.assertEqual(body["in_group_mention_count"], 25)
        self.assertEqual(len(body["items"]), 25)
        self.assertFalse(body["truncated"])

        capped = self.client.get(
            "/api/kg/groups/%d/persons/%d/mentions?limit=7" % (group.id, self.browne.id)
        ).json()
        self.assertEqual(len(capped["items"]), 7)
        self.assertTrue(capped["truncated"])
        self.assertEqual(capped["in_group_mention_count"], 25)

    def test_12_person_mentions_are_scoped_with_locators(self):
        body = self.client.get(
            "/api/kg/groups/%d/persons/%d/mentions" % (self.group_a.id, self.browne.id)
        ).json()
        self.assertEqual(body["display_name"], self.browne.display_name)
        self.assertEqual(len(body["items"]), 1)
        mention = body["items"][0]
        self.assertEqual(mention["document_id"], self.doc_a["document_id"])
        self.assertEqual(mention["block_id"], self.doc_a["block_id"])
        self.assertEqual(mention["page_number"], 1)
        self.assertIn("archival text", mention["evidence_text"])

        outside = self.client.get(
            "/api/kg/groups/%d/persons/%d/mentions" % (self.group_a.id, self.puankhequa.id)
        ).json()
        self.assertEqual(outside["items"], [])

    def test_13_list_endpoints_accept_group_scope(self):
        persons = self.client.get("/api/kg/persons?group_id=%d&limit=200" % self.group_a.id).json()
        self.assertEqual(
            {item["id"] for item in persons["items"]},
            {self.browne.id, self.howqua.id, self.provisional.id},
        )
        accepted = self.client.get(
            "/api/kg/persons?group_id=%d&identity_status=accepted&limit=200" % self.group_a.id
        ).json()
        self.assertEqual(
            {item["id"] for item in accepted["items"]}, {self.browne.id, self.howqua.id}
        )

        official = self.client.get(
            "/api/kg/relationships?group_id=%d&limit=200" % self.group_a.id
        ).json()
        self.assertEqual({item["id"] for item in official["items"]}, {self.rel_a.id})

        candidates = self.client.get(
            "/api/kg/relationships?group_id=%d&view=candidates&limit=200" % self.group_a.id
        ).json()
        self.assertEqual({item["id"] for item in candidates["items"]}, {self.rel_candidate.id})

    def test_15_relationship_evidence_alone_counts_as_extracted(self):
        """A block processed into relationship evidence is extracted even without mentions."""
        document = self._document("Synthetic_KGG_evidence_only.pdf", "E")
        group = self.service.create_group(GroupCreate(name=SYNTH + " Evidence only"))
        self.db.add(
            ResearchGroupDocument(
                group_id=group.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgg",
            )
        )
        self.db.commit()
        relationship = self._relationship(
            self.browne.id, self.howqua.id, ClaimStatus.ACCEPTED, ReviewStatusKG.REVIEWED
        )
        self._evidence(relationship.id, document["block_id"], "evidence-only relation")
        try:
            scope = self.client.get("/api/kg/groups/%d/graph/scope" % group.id).json()
            self.assertEqual(scope["blocks_with_mentions"], 0)
            self.assertEqual(scope["blocks_with_extraction"], 1)
            self.assertEqual(scope["extraction_state"], "ready")

            overview = self.client.get("/api/kg/groups/%d/graph/overview" % group.id).json()
            self.assertEqual(
                {person["id"] for person in overview["persons"]},
                {self.browne.id, self.howqua.id},
            )
            self.assertEqual(
                {rel["id"] for rel in overview["relationships"]}, {relationship.id}
            )
        finally:
            self.db.query(ResearchGroupDocument).filter(
                ResearchGroupDocument.group_id == group.id
            ).delete(synchronize_session=False)
            self.db.query(ResearchGroup).filter(ResearchGroup.id == group.id).delete(
                synchronize_session=False
            )
            self.db.commit()

    def test_16_snapshot_records_the_group_scope(self):
        """P1 groundwork: a saved snapshot must state which scope/ids it came from."""
        response = self.client.post(
            "/api/kg/sna/snapshot",
            json={
                "run_id": "kgg-group-snapshot",
                "graph_scope": "research_group:%d" % self.group_a.id,
                "filters": {"group_id": self.group_a.id, "view": "official"},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

        rows = self.client.get("/api/kg/sna/snapshots/kgg-group-snapshot").json()
        self.assertTrue(rows)
        parameters = rows[0]["parameters"]
        self.assertEqual(parameters["filters"]["group_id"], self.group_a.id)
        self.assertEqual(parameters["filters"]["view"], "official")
        # the snapshot's metrics are the scoped subgraph, not the global one
        self.assertEqual(parameters["summary"]["node_count"], 2)
        self.assertLessEqual(parameters["summary"]["edge_count"], 1)

        # cleanup so the synthetic run does not linger
        from app.models.kg.metrics import SNAGraphMetric

        self.db.query(SNAGraphMetric).filter(
            SNAGraphMetric.run_id == "kgg-group-snapshot"
        ).delete(synchronize_session=False)
        self.db.commit()

    def test_14_rejected_relationship_never_appears(self):
        for view in ("official", "candidates"):
            body = self.client.get(
                "/api/kg/groups/%d/graph/overview?view=%s" % (self.group_a.id, view)
            ).json()
            self.assertNotIn(self.rel_rejected.id, {rel["id"] for rel in body["relationships"]})


if __name__ == "__main__":
    unittest.main()
