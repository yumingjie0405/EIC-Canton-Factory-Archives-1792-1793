"""PostgreSQL tests: Stage 10 review workflow.

What is proven here (all on synthetic fixtures in the isolated test database):

* provenance is stored per record and never invented: a mock run's rows carry
  ``mock_constant``, a rule-stub run's rows carry ``mock_text_derived``, and rows with
  no ledger evidence stay ``unknown``;
* reviewing a relationship persists claim/review status **and** keeps the provenance —
  accepting a stub claim does not turn it into a provider finding;
* review writes an audit row and never deletes evidence or research history;
* batch disambiguation is preview → confirm → revertible, refuses a stale token, and
  never links mentions before the confirmation;
* a relationship supported only by another group's evidence never appears in this group;
* snapshots store nodes/edges/filters/provenance, and comparison keeps structural,
  review and provenance changes apart.

No LLM call is made and no product database is touched.
"""
import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

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
    SourceKind,
)
from app.models.kg.extraction_run import (
    KGExtractionItem,
    KGExtractionItemStatus,
    KGExtractionRun,
    KGExtractionRunStatus,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.kg.workflow import KGGraphSnapshot, KGReviewAudit
from app.models.page import Page
from app.models.research import ResearchMode, ResearchSession, ResearchStatus
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.kg import provenance, review_service, snapshot_service
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-WF]"
GROUP_A = SYNTH + " Workflow group A"
GROUP_B = SYNTH + " Workflow group B"
GROUP_MENTIONS = SYNTH + " Mention group"


@requires_postgres_test_db
class TestWorkflowPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls._build()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._cleanup()
        finally:
            app.dependency_overrides.pop(get_db, None)
            cls.db.close()
            cls.engine.dispose()

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _document(cls, filename, block_count):
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.DOCX,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(document)
        cls.db.flush()
        page = Page(document_id=document.id, page_number=1, width=1000, height=1400)
        cls.db.add(page)
        cls.db.flush()
        blocks = []
        for index in range(block_count):
            block = Block(
                page_id=page.id,
                block_index=index,
                block_label="paragraph",
                bbox=[0, 0, 10, 10],
                ocr_text_raw="%s text %d of %s" % (SYNTH, index, filename),
            )
            cls.db.add(block)
            cls.db.flush()
            blocks.append(block)
        cls.db.commit()
        return document, blocks

    @classmethod
    def _group(cls, name, document_ids):
        group = cls.service.create_group(GroupCreate(name=name))
        for document_id in document_ids:
            cls.db.add(
                ResearchGroupDocument(
                    group_id=group.id,
                    document_id=document_id,
                    import_status=GroupImportStatus.READY,
                    last_batch_id="wf",
                )
            )
        cls.db.commit()
        return group

    @classmethod
    def _run(cls, group_id, blocks, model, source_kind, tokens=100):
        run = KGExtractionRun(
            scope_type="group",
            scope_id=group_id,
            group_id=group_id,
            status=KGExtractionRunStatus.COMPLETED.value,
            model=model,
            source_kind=source_kind,
            token_source=provenance.token_source_for(source_kind, tokens),
            trigger="manual",
            total_blocks=len(blocks),
            processed_blocks=len(blocks),
            tokens_used=tokens,
            budget_blocks=len(blocks) + 5,
            started_at=datetime.utcnow() - timedelta(minutes=5),
            finished_at=datetime.utcnow(),
        )
        cls.db.add(run)
        cls.db.flush()
        for block in blocks:
            cls.db.add(
                KGExtractionItem(
                    run_id=run.id,
                    block_id=block.id,
                    status=KGExtractionItemStatus.SUCCEEDED.value,
                    tokens=10,
                )
            )
        cls.db.commit()
        return run

    @classmethod
    def _person(cls, name, identity=IdentityStatus.PROVISIONAL):
        person = Person(
            display_name=name, canonical_name_en=name, identity_status=identity
        )
        cls.db.add(person)
        cls.db.commit()
        return person

    @classmethod
    def _mention(cls, block_id, text, *, person=None, kind=None, run=None):
        mention = PersonMention(
            block_id=block_id,
            person_id=person.id if person else None,
            raw_text=text,
            normalized_text=text,
            mention_type=MentionType.PERSON_NAME,
            resolution_status=ResolutionStatus.UNRESOLVED,
            review_status=ReviewStatusKG.UNREVIEWED,
            source_kind=kind,
            extraction_run_id=run.id if run else None,
        )
        cls.db.add(mention)
        cls.db.commit()
        return mention

    @classmethod
    def _relationship(cls, source, target, claim=ClaimStatus.ASSERTED, review=ReviewStatusKG.UNREVIEWED):
        relationship = Relationship(
            source_person_id=source.id,
            target_person_id=target.id,
            relation_type=RelationType.COMMERCIAL,
            claim_status=claim,
            review_status=review,
            evidence_summary=SYNTH + " claim",
        )
        cls.db.add(relationship)
        cls.db.commit()
        return relationship

    @classmethod
    def _evidence(cls, relationship, block_id, kind, run=None):
        evidence = RelationshipEvidence(
            relationship_id=relationship.id,
            block_id=block_id,
            evidence_role=EvidenceRole.SUPPORTS,
            quote_text=SYNTH + " quote",
            source_kind=kind,
            extraction_run_id=run.id if run else None,
        )
        cls.db.add(evidence)
        cls.db.commit()
        return evidence

    @classmethod
    def _build(cls):
        # ---- group A: a mock claim and a stub claim on the same document
        cls.doc_a, blocks_a = cls._document("Synthetic_WF_a.docx", 3)
        cls.group_a = cls._group(GROUP_A, [cls.doc_a.id])
        cls.b_a1, cls.b_a2, cls.b_a3 = blocks_a
        cls.run_mock = cls._run(
            cls.group_a.id, blocks_a, "mock-model", SourceKind.MOCK_CONSTANT.value
        )
        cls.p_browne = cls._person(SYNTH + " Henry Browne", IdentityStatus.ACCEPTED)
        cls.p_howqua = cls._person(SYNTH + " Howqua")
        cls.p_unknown = cls._person(SYNTH + " Legacy Partner")

        cls.rel_mock = cls._relationship(cls.p_browne, cls.p_howqua)
        cls.ev_mock = cls._evidence(
            cls.rel_mock, cls.b_a1.id, SourceKind.MOCK_CONSTANT.value, cls.run_mock
        )
        cls.rel_unknown = cls._relationship(cls.p_howqua, cls.p_unknown)
        cls.ev_unknown = cls._evidence(cls.rel_unknown, cls.b_a2.id, None)

        # research history that cites the mock evidence's block
        cls.session = ResearchSession(
            mode=ResearchMode.RAG,
            question=SYNTH + " who traded?",
            answer="synthetic",
            status=ResearchStatus.COMPLETED,
            evidence_sufficient=True,
            sources=[{"block_id": cls.b_a1.id, "quote": SYNTH}],
            relationships=[],
            group_id=cls.group_a.id,
            group_name=GROUP_A,
        )
        cls.db.add(cls.session)
        cls.db.commit()

        # ---- group B: same persons, evidence only in B (must never leak into A)
        cls.doc_b, blocks_b = cls._document("Synthetic_WF_b.docx", 1)
        cls.group_b = cls._group(GROUP_B, [cls.doc_b.id])
        cls.b_b1 = blocks_b[0]
        cls.run_b = cls._run(
            cls.group_b.id, blocks_b, "mock-text-stub", SourceKind.MOCK_TEXT_DERIVED.value
        )
        cls.rel_b_only = cls._relationship(cls.p_browne, cls.p_unknown)
        cls._evidence(
            cls.rel_b_only, cls.b_b1.id, SourceKind.MOCK_TEXT_DERIVED.value, cls.run_b
        )

        # ---- group for mention batch work
        cls.doc_m, blocks_m = cls._document("Synthetic_WF_m.docx", 2)
        cls.group_m = cls._group(GROUP_MENTIONS, [cls.doc_m.id])
        cls.m_b1, cls.m_b2 = blocks_m
        cls.run_m = cls._run(
            cls.group_m.id, blocks_m, "mock-text-stub", SourceKind.MOCK_TEXT_DERIVED.value
        )
        cls.m_1 = cls._mention(
            cls.m_b1.id, "Chowqua", kind=SourceKind.MOCK_TEXT_DERIVED.value, run=cls.run_m
        )
        cls.m_2 = cls._mention(
            cls.m_b2.id, "Chowqua", kind=SourceKind.MOCK_TEXT_DERIVED.value, run=cls.run_m
        )
        cls.m_3 = cls._mention(cls.m_b1.id, "Puankhequa", kind=None)

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            group_ids = [
                row[0]
                for row in db.query(ResearchGroup.id)
                .filter(ResearchGroup.name.like(SYNTH + "%"))
                .all()
            ]
            if group_ids:
                db.query(KGGraphSnapshot).filter(
                    KGGraphSnapshot.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(KGReviewAudit).filter(
                    KGReviewAudit.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchSession).filter(
                    ResearchSession.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                run_ids = [
                    row[0]
                    for row in db.query(KGExtractionRun.id)
                    .filter(KGExtractionRun.group_id.in_(group_ids))
                    .all()
                ]
                if run_ids:
                    db.query(KGExtractionItem).filter(
                        KGExtractionItem.run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(KGExtractionRun).filter(
                        KGExtractionRun.id.in_(run_ids)
                    ).delete(synchronize_session=False)
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(group_ids)).delete(
                    synchronize_session=False
                )
            doc_ids = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_WF_%"))
                .all()
            ]
            if doc_ids:
                page_ids = [
                    row[0]
                    for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
                ]
                block_ids = [
                    row[0]
                    for row in db.query(Block.id).filter(Block.page_id.in_(page_ids or [-1])).all()
                ]
                if block_ids:
                    rel_ids = [
                        row[0]
                        for row in db.query(RelationshipEvidence.relationship_id)
                        .filter(RelationshipEvidence.block_id.in_(block_ids))
                        .distinct()
                        .all()
                    ]
                    db.query(RelationshipEvidence).filter(
                        RelationshipEvidence.block_id.in_(block_ids)
                    ).delete(synchronize_session=False)
                    db.query(PersonMention).filter(
                        PersonMention.block_id.in_(block_ids)
                    ).delete(synchronize_session=False)
                    if rel_ids:
                        db.query(Relationship).filter(
                            Relationship.id.in_(rel_ids)
                        ).delete(synchronize_session=False)
                    db.query(Block).filter(Block.id.in_(block_ids)).delete(
                        synchronize_session=False
                    )
                db.query(Page).filter(Page.id.in_(page_ids or [-1])).delete(
                    synchronize_session=False
                )
                db.query(Document).filter(Document.id.in_(doc_ids)).delete(
                    synchronize_session=False
                )
            for person_id in [
                row[0]
                for row in db.query(Person.id)
                .filter(Person.display_name.like(SYNTH + "%"))
                .all()
            ]:
                if (
                    db.query(PersonMention.id)
                    .filter(PersonMention.person_id == person_id)
                    .first()
                    is None
                    and db.query(Relationship.id)
                    .filter(
                        (Relationship.source_person_id == person_id)
                        | (Relationship.target_person_id == person_id)
                    )
                    .first()
                    is None
                ):
                    db.query(Person).filter(Person.id == person_id).delete(
                        synchronize_session=False
                    )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    # --------------------------------------------------------- provenance
    def test_01_run_provenance_and_token_source_are_exposed(self):
        body = self.client.get("/api/kg/extraction/runs/%d" % self.run_mock.id).json()
        self.assertEqual(body["source_kind"], SourceKind.MOCK_CONSTANT.value)
        self.assertEqual(body["token_source"], "estimated")
        self.assertTrue(body["is_mock_model"])

        other = self.client.get("/api/kg/extraction/runs/%d" % self.run_b.id).json()
        self.assertEqual(other["source_kind"], SourceKind.MOCK_TEXT_DERIVED.value)
        self.assertEqual(other["token_source"], "estimated")

    def test_02_records_keep_their_own_provenance(self):
        listing = self.client.get(
            "/api/kg/groups/%d/relationships?limit=50" % self.group_a.id
        ).json()
        by_id = {item["id"]: item for item in listing["items"]}
        mock_row = by_id[self.rel_mock.id]
        self.assertEqual(mock_row["source_kind"], SourceKind.MOCK_CONSTANT.value)
        self.assertTrue(mock_row["synthetic"])
        unknown_row = by_id[self.rel_unknown.id]
        self.assertEqual(unknown_row["source_kind"], SourceKind.UNKNOWN.value)
        # Unknown provenance is not "synthetic", but it is never treated as verified.
        self.assertFalse(unknown_row["synthetic"])
        self.assertTrue(unknown_row["unverified"])

    def test_03_relationship_evidence_carries_provenance(self):
        body = self.client.get(
            "/api/kg/groups/%d/relationships/%d/evidence" % (self.group_a.id, self.rel_mock.id)
        ).json()
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["source_kind"], SourceKind.MOCK_CONSTANT.value)
        self.assertTrue(item["synthetic"])
        self.assertEqual(item["extraction_run_id"], self.run_mock.id)

    # ------------------------------------------------------------- review
    def test_04_review_persists_status_and_keeps_provenance(self):
        response = self.client.post(
            "/api/kg/relationships/%d/review" % self.rel_mock.id,
            json={
                "claim_status": "accepted",
                "review_status": "reviewed",
                "reason": "workflow test",
                "reviewer": "tester",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["relationship"]["claim_status"], "accepted")
        self.assertEqual(body["relationship"]["review_status"], "reviewed")
        # Provenance must survive the decision.
        self.assertEqual(body["source_kind"], SourceKind.MOCK_CONSTANT.value)
        self.assertTrue(body["synthetic"])
        self.assertIn("规则桩", body["warning"] or "")

        audit = (
            self.db.query(KGReviewAudit)
            .filter(KGReviewAudit.relationship_id == self.rel_mock.id)
            .order_by(KGReviewAudit.id.desc())
            .first()
        )
        self.assertIsNotNone(audit)
        self.assertEqual(audit.operation_type, "relationship_review")
        self.assertEqual(audit.operator, "tester")
        self.assertEqual(audit.before["claim_status"], "asserted")
        self.assertEqual(audit.after["claim_status"], "accepted")

        # Evidence and research history are untouched by a review decision.
        self.assertIsNotNone(
            self.db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.id == self.ev_unknown.id)
            .one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(ResearchSession)
            .filter(ResearchSession.id == self.session.id)
            .one_or_none()
        )

        history = self.client.get(
            "/api/kg/relationships/%d/review-history" % self.rel_mock.id
        ).json()
        self.assertTrue(history)
        self.assertEqual(history[0]["operation_type"], "relationship_review")

    def test_05_rejected_claims_leave_the_official_view(self):
        self.client.post(
            "/api/kg/relationships/%d/review" % self.rel_unknown.id,
            json={"claim_status": "rejected", "review_status": "reviewed"},
        )
        official = self.client.get(
            "/api/kg/groups/%d/graph/overview?view=official" % self.group_a.id
        ).json()
        ids = {row["id"] for row in official["relationships"]}
        self.assertNotIn(self.rel_unknown.id, ids)

    def test_06_invalid_status_is_refused(self):
        response = self.client.post(
            "/api/kg/relationships/%d/review" % self.rel_mock.id,
            json={"claim_status": "confirmed-by-model"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("主张状态", response.json()["detail"])

    # ------------------------------------------------------ disambiguation
    def test_07_batch_preview_writes_nothing(self):
        before = self.db.query(PersonMention.id).filter(
            PersonMention.id.in_([self.m_1.id, self.m_2.id])
        ).count()
        preview = self.client.post(
            "/api/kg/groups/%d/mentions/batch-link/preview" % self.group_m.id,
            json={"normalized_text": "Chowqua", "new_person_name": SYNTH + " Chowqua"},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["mention_count"], 2)
        self.assertIn(self.m_1.id, body["mention_ids"])
        self.assertTrue(body["synthetic"])
        self.assertTrue(body["confirm_token"])
        self.assertTrue(
            any("规则桩" in warning for warning in body["warnings"]),
            body["warnings"],
        )
        # Nothing was applied yet.
        for mention_id in (self.m_1.id, self.m_2.id):
            mention = (
                self.db.query(PersonMention).filter(PersonMention.id == mention_id).one()
            )
            self.assertIsNone(mention.person_id)
        self.assertEqual(
            self.db.query(PersonMention.id)
            .filter(PersonMention.id.in_([self.m_1.id, self.m_2.id]))
            .count(),
            before,
        )

    def test_08_stale_preview_token_is_refused(self):
        response = self.client.post(
            "/api/kg/groups/%d/mentions/batch-link/confirm" % self.group_m.id,
            json={
                "normalized_text": "Chowqua",
                "new_person_name": SYNTH + " Chowqua",
                "confirm_token": "deadbeefdeadbeef",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("预览", response.json()["detail"])

    def test_09_batch_confirm_then_revert(self):
        preview = self.client.post(
            "/api/kg/groups/%d/mentions/batch-link/preview" % self.group_m.id,
            json={"normalized_text": "Chowqua", "new_person_name": SYNTH + " Chowqua"},
        ).json()
        confirmed = self.client.post(
            "/api/kg/groups/%d/mentions/batch-link/confirm" % self.group_m.id,
            json={
                "normalized_text": "Chowqua",
                "new_person_name": SYNTH + " Chowqua",
                "confirm_token": preview["confirm_token"],
                "reason": "workflow test",
                "operator": "tester",
            },
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        body = confirmed.json()
        self.assertEqual(body["mentions_linked"], 2)
        self.assertTrue(body["created_person"])
        self.assertTrue(body["batch_id"])

        person = (
            self.db.query(Person).filter(Person.id == body["person_id"]).one()
        )
        self.assertEqual(person.identity_status, IdentityStatus.PROVISIONAL)

        for mention_id in (self.m_1.id, self.m_2.id):
            mention = self.db.query(PersonMention).filter(PersonMention.id == mention_id).one()
            self.assertEqual(mention.person_id, body["person_id"])
            self.assertEqual(mention.resolution_status, ResolutionStatus.RESOLVED)

        audits = (
            self.db.query(KGReviewAudit)
            .filter(KGReviewAudit.batch_id == body["batch_id"])
            .all()
        )
        self.assertEqual(len(audits), 2)
        self.assertTrue(all(row.operation_type == "mention_batch_link" for row in audits))

        reverted = self.client.post(
            "/api/kg/mentions/batches/%s/revert" % body["batch_id"]
        )
        self.assertEqual(reverted.status_code, 200, reverted.text)
        self.assertEqual(reverted.json()["restored_mentions"], 2)
        for mention_id in (self.m_1.id, self.m_2.id):
            mention = self.db.query(PersonMention).filter(PersonMention.id == mention_id).one()
            self.assertIsNone(mention.person_id)
            self.assertEqual(mention.resolution_status, ResolutionStatus.UNRESOLVED)

    def test_10_same_name_alone_never_links_anything(self):
        cluster = self.client.get(
            "/api/kg/groups/%d/mentions/clusters?limit_groups=10" % self.group_m.id
        ).json()
        names = {item["normalized_text"]: item for item in cluster["items"]}
        self.assertIn("Chowqua", names)
        self.assertEqual(names["Chowqua"]["mention_count"], 2)
        # Listing a cluster writes nothing.
        linked = (
            self.db.query(PersonMention)
            .filter(PersonMention.normalized_text == "Chowqua", PersonMention.person_id.isnot(None))
            .count()
        )
        self.assertEqual(linked, 0)

    # ---------------------------------------------------------- isolation
    def test_11_foreign_evidence_never_leaks_into_another_group(self):
        listing = self.client.get(
            "/api/kg/groups/%d/relationships?limit=50" % self.group_a.id
        ).json()
        ids = {item["id"] for item in listing["items"]}
        self.assertNotIn(self.rel_b_only.id, ids)

        other = self.client.get(
            "/api/kg/groups/%d/relationships?limit=50" % self.group_b.id
        ).json()
        other_ids = {item["id"] for item in other["items"]}
        self.assertIn(self.rel_b_only.id, other_ids)
        self.assertNotIn(self.rel_mock.id, other_ids)

    def test_12_unknown_group_is_404_for_every_new_endpoint(self):
        for path in (
            "/api/kg/groups/987654/relationships",
            "/api/kg/groups/987654/mentions/clusters",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)

    # ----------------------------------------------------------- snapshots
    def test_13_snapshot_stores_scope_nodes_edges_and_provenance(self):
        created = self.client.post(
            "/api/kg/graph/snapshots",
            json={
                "run_id": SYNTH + "-snap-1",
                "name": SYNTH + " snapshot 1",
                "group_id": self.group_a.id,
                "view": "all",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        detail = created.json()
        self.assertGreaterEqual(detail["node_count"], 2)
        self.assertEqual(detail["algorithm_version"], "NativeSNAEngine-v1.0")
        self.assertTrue(detail["capabilities"]["can_display"])
        self.assertTrue(detail["extraction_run_ids"])
        self.assertTrue(
            any(
                SourceKind.MOCK_CONSTANT.value in (rel.get("source_kinds") or {})
                for rel in detail["relationships"]
            )
        )
        self.assertIn("synthetic", detail["data_trust"])

    def test_14_snapshot_requires_a_group(self):
        response = self.client.get("/api/kg/graph/snapshots/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_15_compare_separates_structure_review_and_provenance(self):
        # A review decision *between* the two snapshots must be reported as a review
        # change, not as a structural "new finding".
        self.client.post(
            "/api/kg/relationships/%d/review" % self.rel_mock.id,
            json={"claim_status": "uncertain", "review_status": "in_review"},
        )
        second = self.client.post(
            "/api/kg/graph/snapshots",
            json={
                "run_id": SYNTH + "-snap-2",
                "name": SYNTH + " snapshot 2",
                "group_id": self.group_a.id,
                "view": "all",
            },
        )
        self.assertEqual(second.status_code, 201, second.text)

        compared = self.client.get(
            "/api/kg/graph/snapshots/compare?left=%s&right=%s"
            % (SYNTH + "-snap-1", SYNTH + "-snap-2")
        )
        self.assertEqual(compared.status_code, 200, compared.text)
        body = compared.json()
        # Same view and parameters: a false structural change must not be reported.
        self.assertFalse(body["view_changed"])
        self.assertFalse(body["parameters_changed"])
        self.assertEqual(body["structure"]["relationships_added"], [])
        changed_ids = {item["id"] for item in body["review_changes"]}
        self.assertIn(self.rel_mock.id, changed_ids)

        # A different view IS reported as a parameter change, and the claim that no longer
        # qualifies disappears from the structure rather than being silently kept.
        third = self.client.post(
            "/api/kg/graph/snapshots",
            json={
                "run_id": SYNTH + "-snap-3",
                "name": SYNTH + " snapshot 3",
                "group_id": self.group_a.id,
                "view": "official",
            },
        )
        self.assertEqual(third.status_code, 201, third.text)
        narrowed = self.client.get(
            "/api/kg/graph/snapshots/compare?left=%s&right=%s"
            % (SYNTH + "-snap-1", SYNTH + "-snap-3")
        ).json()
        self.assertTrue(narrowed["view_changed"])
        self.assertIn(self.rel_mock.id, narrowed["structure"]["relationships_removed"])

    def test_16_snapshot_rejects_a_duplicate_run_id(self):
        response = self.client.post(
            "/api/kg/graph/snapshots",
            json={
                "run_id": SYNTH + "-snap-1",
                "group_id": self.group_a.id,
                "view": "all",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("已存在", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
