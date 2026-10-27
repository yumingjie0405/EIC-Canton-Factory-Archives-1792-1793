"""PostgreSQL tests: evidence accounting and SNA input transparency.

What is proven here:

* a claim repeated verbatim across many blocks reports *distinct archival locators*
  and *stored rows* as two separate numbers, so "14 620 条证据" can never again be
  read as 14 620 independent historical findings;
* the provenance split (verified / mock / unknown) is computed per relationship and
  restricted to the group's own blocks — foreign evidence never inflates the counters;
* the SNA payload states the composition of the edges it measured, so a network built
  from mock edges cannot be presented as a historical result.

Only the database is exercised; no LLM call is made.
"""
import unittest

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
    RelationType,
    ReviewStatusKG,
    SourceKind,
)
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.kg_sna import SNAGraphFilterParams
from app.schemas.research_group import GroupCreate
from app.services.kg.group_graph import (
    relationship_evidence_stats,
    load_scope,
    scope_overview,
)
from app.services.kg.sna_service import sna_service
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-ACC]"
GROUP_MAIN = SYNTH + " Evidence group"
GROUP_OTHER = SYNTH + " Other group"


@requires_postgres_test_db
class TestKgEvidenceAccountingPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()

        # Main group: 4 blocks in one document; other group: 1 block.
        cls.doc_main = cls._document("Synthetic_ACC_main.pdf", 4)
        cls.doc_other = cls._document("Synthetic_ACC_other.pdf", 1)

        cls.group_main = cls.service.create_group(GroupCreate(name=GROUP_MAIN))
        cls.group_other = cls.service.create_group(GroupCreate(name=GROUP_OTHER))
        for group, doc in (
            (cls.group_main, cls.doc_main),
            (cls.group_other, cls.doc_other),
        ):
            cls.db.add(
                ResearchGroupDocument(
                    group_id=group.id,
                    document_id=doc["document_id"],
                    import_status=GroupImportStatus.READY,
                    last_batch_id="acc",
                )
            )
        cls.db.commit()

        cls.nelson = cls._person(SYNTH + " Nelson")
        cls.anker = cls._person(SYNTH + " Anker")

        # A claim supported by 3 blocks with the *same* quote (mock-style repetition)
        # plus 1 block with a different quote.
        cls.rel_repeat = cls._relationship(cls.nelson.id, cls.anker.id)
        quote = SYNTH + " repeated contract clause"
        for block_id in cls.doc_main["block_ids"][:3]:
            cls._evidence(
                cls.rel_repeat.id,
                block_id,
                quote,
                SourceKind.MOCK_TEXT_DERIVED.value,
            )
        cls._evidence(
            cls.rel_repeat.id,
            cls.doc_main["block_ids"][3],
            SYNTH + " a second, distinct clause",
            SourceKind.PROVIDER_REPORTED.value,
        )
        # One evidence row whose origin cannot be reconstructed.
        cls._evidence(
            cls.rel_repeat.id,
            cls.doc_main["block_ids"][0],
            SYNTH + " unattributed clause",
            None,
        )
        # Foreign evidence for the *same* claim: must never be counted in this group.
        cls._evidence(
            cls.rel_repeat.id,
            cls.doc_other["block_ids"][0],
            SYNTH + " foreign clause",
            SourceKind.PROVIDER_REPORTED.value,
        )

        # A second claim entirely mock-derived, evidence only in the foreign group.
        cls.rel_foreign = cls._relationship(cls.anker.id, cls.nelson.id)
        cls._evidence(
            cls.rel_foreign.id,
            cls.doc_other["block_ids"][0],
            SYNTH + " foreign-only claim",
            SourceKind.MOCK_CONSTANT.value,
        )
        cls.db.commit()

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _document(cls, filename, block_count):
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
        for index in range(block_count):
            block = Block(
                page_id=page.id,
                block_index=index,
                block_label="paragraph",
                bbox=[0, 0, 10, 10],
                ocr_text_raw="%s archival text %d" % (SYNTH, index),
            )
            cls.db.add(block)
            cls.db.flush()
            block_ids.append(block.id)
        cls.db.commit()
        return {"document_id": document.id, "page_id": page.id, "block_ids": block_ids}

    @classmethod
    def _person(cls, name):
        person = Person(
            display_name=name,
            canonical_name_en=name,
            primary_role="Supercargo",
            identity_status=IdentityStatus.PROVISIONAL,
        )
        cls.db.add(person)
        cls.db.commit()
        return person

    @classmethod
    def _relationship(cls, source_id, target_id):
        relationship = Relationship(
            source_person_id=source_id,
            target_person_id=target_id,
            relation_type=RelationType.COMMERCIAL,
            claim_status=ClaimStatus.ASSERTED,
            review_status=ReviewStatusKG.UNREVIEWED,
            confidence=0.5,
            evidence_summary=SYNTH + " synthetic relation",
        )
        cls.db.add(relationship)
        cls.db.commit()
        return relationship

    @classmethod
    def _evidence(cls, relationship_id, block_id, quote, source_kind):
        cls.db.add(
            RelationshipEvidence(
                relationship_id=relationship_id,
                block_id=block_id,
                quote_text=quote,
                evidence_role=EvidenceRole.SUPPORTS,
                source_kind=source_kind,
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
                .filter(ResearchGroup.name.in_([GROUP_MAIN, GROUP_OTHER]))
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
                db.query(Person).filter(Person.id.in_(person_ids)).delete(
                    synchronize_session=False
                )
            doc_ids = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_ACC_%"))
                .all()
            ]
            if doc_ids:
                page_ids = [
                    row[0] for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
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

    # --------------------------------------------------------------- stats
    def test_repetition_is_counted_as_locators_not_findings(self):
        stats = relationship_evidence_stats(
            self.db, [self.rel_repeat.id], load_scope(self.db, self.group_main.id)
        )
        entry = stats[self.rel_repeat.id]
        # 5 in-group rows: 3 identical quotes, 1 distinct quote, 1 unattributed.
        self.assertEqual(entry["evidence_rows"], 5)
        self.assertEqual(entry["distinct_blocks"], 4)
        self.assertEqual(entry["distinct_quotes"], 3)
        self.assertLess(entry["distinct_quotes"], entry["evidence_rows"])

    def test_foreign_evidence_is_excluded_from_group_counters(self):
        stats = relationship_evidence_stats(
            self.db, [self.rel_repeat.id], load_scope(self.db, self.group_main.id)
        )
        # The 6th row lives in the other group and must not be counted here.
        self.assertEqual(stats[self.rel_repeat.id]["evidence_rows"], 5)
        unscoped = relationship_evidence_stats(self.db, [self.rel_repeat.id])
        self.assertEqual(unscoped[self.rel_repeat.id]["evidence_rows"], 6)

    def test_provenance_buckets_split_rows(self):
        stats = relationship_evidence_stats(
            self.db, [self.rel_repeat.id], load_scope(self.db, self.group_main.id)
        )
        entry = stats[self.rel_repeat.id]
        self.assertEqual(entry["synthetic"], 3)
        self.assertEqual(entry["verified"], 1)
        self.assertEqual(entry["unknown"], 1)
        self.assertEqual(
            entry["synthetic"] + entry["verified"] + entry["unknown"], entry["evidence_rows"]
        )

    def test_group_without_blocks_has_no_evidence_counters(self):
        empty = self.service.create_group(GroupCreate(name=SYNTH + " No blocks"))
        try:
            stats = relationship_evidence_stats(
                self.db, [self.rel_repeat.id], load_scope(self.db, empty.id)
            )
            self.assertEqual(stats, {})
            self.assertEqual(stats.get(self.rel_repeat.id, {}), {})
        finally:
            self.db.query(ResearchGroup).filter(ResearchGroup.id == empty.id).delete(
                synchronize_session=False
            )
            self.db.commit()

    def test_scope_overview_totals_match_per_relationship_stats(self):
        overview = scope_overview(self.db, load_scope(self.db, self.group_main.id))
        totals = overview["evidence_bucket_totals"]
        self.assertEqual(overview["evidence_rows_total"], 5)
        self.assertEqual(
            totals["synthetic"] + totals["verified"] + totals["unknown"],
            overview["evidence_rows_total"],
        )

    # ----------------------------------------------------------------- API
    def test_overview_endpoint_exposes_distinct_counts(self):
        response = self.client.get(
            f"/api/kg/groups/{self.group_main.id}/graph/overview",
            params={"view": "all", "include_provisional": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["evidence_rows_total"], 5)
        self.assertEqual(payload["synthetic_evidence_rows"], 3)
        self.assertEqual(payload["verified_evidence_rows"], 1)
        self.assertEqual(payload["unknown_evidence_rows"], 1)
        self.assertIn("evidence_count", payload["evidence_note"])

        relationship = next(
            item for item in payload["relationships"] if item["id"] == self.rel_repeat.id
        )
        self.assertEqual(relationship["evidence_rows"], 5)
        self.assertEqual(relationship["evidence_count"], 4)
        self.assertEqual(relationship["distinct_quotes"], 3)
        self.assertEqual(relationship["synthetic_evidence_count"], 3)
        self.assertEqual(relationship["verified_evidence_count"], 1)
        self.assertEqual(relationship["unknown_evidence_count"], 1)

    def test_evidence_endpoint_reports_distinct_locators(self):
        response = self.client.get(
            f"/api/kg/groups/{self.group_main.id}/relationships/{self.rel_repeat.id}/evidence"
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["in_group_evidence_count"], 5)
        self.assertEqual(payload["distinct_block_count"], 4)
        self.assertEqual(payload["distinct_quote_count"], 3)
        self.assertEqual(payload["out_of_group_evidence_count"], 1)
        self.assertEqual(payload["synthetic_evidence_count"], 3)
        self.assertEqual(payload["verified_evidence_count"], 1)
        self.assertEqual(payload["unknown_evidence_count"], 1)

    def test_sna_payload_states_edge_provenance(self):
        summary = sna_service.get_network_summary(
            self.db,
            SNAGraphFilterParams(
                group_id=self.group_main.id, view="all", include_provisional=True
            ),
        )
        self.assertEqual(summary.edge_count, 1)
        # Mixed provenance (mock + provider + unattributed) must never resolve to
        # "verified": it is reported as unknown, with mock rows still visible.
        self.assertEqual(summary.verified_edge_count, 0)
        self.assertEqual(summary.synthetic_edge_count, 0)
        self.assertEqual(summary.unknown_edge_count, 1)
        self.assertEqual(
            summary.edge_source_composition.get(SourceKind.UNKNOWN.value), 1
        )
        self.assertIn("Mock", summary.metrics_basis)

    def test_sna_composition_excludes_foreign_claims(self):
        summary = sna_service.get_network_summary(
            self.db,
            SNAGraphFilterParams(
                group_id=self.group_other.id, view="all", include_provisional=True
            ),
        )
        # In the other group the repeated claim is supported only by its one provider
        # row, and the foreign-only claim only by mock evidence.
        self.assertEqual(summary.edge_count, 2)
        self.assertEqual(summary.verified_edge_count, 1)
        self.assertEqual(summary.synthetic_edge_count, 1)
        self.assertEqual(summary.unknown_edge_count, 0)


if __name__ == "__main__":
    unittest.main()
