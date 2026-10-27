"""PostgreSQL tests: audited cleanup of mock-derived extraction records.

The tests never delete production-like data: every scenario is built in the isolated
test database, and the "execute" path is exercised on those synthetic rows only.

What is proven
--------------
* a dry-run writes **nothing** (no rows, no audit, no run stamps);
* only records whose block was processed by the targeted run *inside* that run's window
  are provable;
* records with a different timestamp, on a non-succeeded block, or on a block another
  run also processed are ambiguous and never deleted;
* accepted relationships and accepted persons are protected, and the evidence of an
  accepted claim is kept as the trace of that decision;
* a relationship that still has evidence of unknown provenance is protected, **but** the
  evidence that provably belongs to the mock run is still removable: relationship
  protection and evidence protection are decided separately;
* persons still referenced by any mention/relationship/audit stay;
* run rows and their items survive; the run is stamped with reset_at/reset_reason and a
  dedicated audit row is written;
* a saved research record citing a *block* does not protect the derived KG rows written
  onto that block (it stores block_id and its own quote copy); only an id reference to a
  mention/evidence/relationship protects that record;
* other groups are untouched;
* execution needs the exact confirmation name plus a reason, and refuses the product
  database;
* an error during execution rolls the whole transaction back.
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentType
from app.models.kg.enums import (
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.models.kg.extraction_run import (
    KGExtractionItem,
    KGExtractionItemStatus,
    KGExtractionResetAudit,
    KGExtractionRun,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.models.research import ResearchMode, ResearchSession, ResearchStatus
from app.models.research_group import ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.kg import extraction_reset
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-RST]"
GROUP_MAIN = SYNTH + " Cleanup group"
GROUP_OTHER = SYNTH + " Untouched group"
GROUP_RESEARCH = SYNTH + " Research-referenced group"
GROUP_NOISE = SYNTH + " Time-overlap group"
GROUP_REFS = SYNTH + " Direct-reference group"

NOW = datetime.utcnow()
WINDOW_START = NOW - timedelta(hours=2)
WINDOW_END = NOW - timedelta(hours=1)
INSIDE = NOW - timedelta(minutes=90)
OUTSIDE = NOW - timedelta(days=3)


@requires_postgres_test_db
class TestExtractionResetPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls._build_main_scenario()
        cls._build_other_group()
        cls._build_research_scenario()
        cls._build_noise_group()
        cls._build_reference_group()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._cleanup()
        finally:
            app.dependency_overrides.pop(get_db, None)
            cls.db.close()
            cls.engine.dispose()

    # ------------------------------------------------------------- builders
    @classmethod
    def _document(cls, filename, block_count):
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.DOCX,
            file_size=1024,
            page_count=1,
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
                ResearchGroupDocument(group_id=group.id, document_id=document_id)
            )
        cls.db.commit()
        return group

    @classmethod
    def _run(cls, group_id, blocks, *, status="completed", model="mock-model", succeeded=None):
        run = KGExtractionRun(
            scope_type="group",
            scope_id=group_id,
            group_id=group_id,
            status=status,
            model=model,
            trigger="manual",
            budget_blocks=len(blocks) + 10,
            budget_source="explicit",
            total_blocks=len(blocks),
            processed_blocks=len(succeeded) if succeeded is not None else len(blocks),
            started_at=WINDOW_START,
            finished_at=WINDOW_END,
            created_at=WINDOW_START,
        )
        cls.db.add(run)
        cls.db.flush()
        for block in blocks:
            ok = succeeded is None or block.id in succeeded
            cls.db.add(
                KGExtractionItem(
                    run_id=run.id,
                    block_id=block.id,
                    status=(
                        KGExtractionItemStatus.SUCCEEDED.value
                        if ok
                        else KGExtractionItemStatus.FAILED.value
                    ),
                    tokens=10,
                )
            )
        cls.db.commit()
        return run

    @classmethod
    def _mention(cls, block_id, *, created_at, person=None):
        mention = PersonMention(
            block_id=block_id,
            person_id=person.id if person else None,
            raw_text="%s mention" % SYNTH,
            normalized_text="%s mention" % SYNTH,
            mention_type=MentionType.PERSON_NAME,
            resolution_status=(
                ResolutionStatus.RESOLVED if person else ResolutionStatus.UNRESOLVED
            ),
            review_status=ReviewStatusKG.UNREVIEWED,
            created_at=created_at,
        )
        cls.db.add(mention)
        cls.db.commit()
        return mention

    @classmethod
    def _person(cls, name, *, identity=IdentityStatus.PROVISIONAL):
        person = Person(
            display_name=name,
            canonical_name_en=name,
            identity_status=identity,
        )
        cls.db.add(person)
        cls.db.commit()
        return person

    @classmethod
    def _relationship(cls, source, target, *, claim=ClaimStatus.ASSERTED):
        relationship = Relationship(
            source_person_id=source.id,
            target_person_id=target.id,
            relation_type=RelationType.COMMERCIAL,
            claim_status=claim,
            review_status=ReviewStatusKG.UNREVIEWED,
            confidence=0.8,
            evidence_summary=SYNTH + " synthetic",
        )
        cls.db.add(relationship)
        cls.db.commit()
        return relationship

    @classmethod
    def _evidence(cls, relationship, block_id, *, created_at):
        evidence = RelationshipEvidence(
            relationship_id=relationship.id,
            block_id=block_id,
            quote_text=SYNTH + " quote",
            evidence_role=EvidenceRole.SUPPORTS,
            created_at=created_at,
        )
        cls.db.add(evidence)
        cls.db.commit()
        return evidence

    @classmethod
    def _build_main_scenario(cls):
        cls.doc_main, blocks = cls._document("Synthetic_RST_main.docx", 3)
        cls.group_main = cls._group(GROUP_MAIN, [cls.doc_main.id])
        b1, b2, b3 = blocks
        cls.b1, cls.b2, cls.b3 = b1, b2, b3
        # b1/b2 succeeded inside the window, b3 failed -> its records are not provable.
        cls.run_main = cls._run(
            cls.group_main.id, blocks, succeeded={b1.id, b2.id}
        )

        cls.henry = cls._person(SYNTH + " Henry Browne", identity=IdentityStatus.ACCEPTED)
        cls.p_new = cls._person(SYNTH + " Fresh Witness")
        cls.p_shared = cls._person(SYNTH + " Shared Witness")
        cls.p_endpoint = cls._person(SYNTH + " Endpoint Only")

        cls.m_provable = cls._mention(b1.id, created_at=INSIDE, person=cls.p_new)
        cls.m_outside = cls._mention(b2.id, created_at=OUTSIDE, person=cls.p_shared)
        cls.m_not_succeeded = cls._mention(b3.id, created_at=INSIDE)
        cls._mention(b1.id, created_at=INSIDE, person=cls.p_shared)

        # rel_ok: only provable evidence -> deletable
        cls.rel_ok = cls._relationship(cls.p_new, cls.p_endpoint)
        cls.e_provable = cls._evidence(cls.rel_ok, b1.id, created_at=INSIDE)
        # rel_mixed: one provable + one ambiguous evidence -> protected
        cls.rel_mixed = cls._relationship(cls.p_new, cls.p_shared)
        cls._evidence(cls.rel_mixed, b2.id, created_at=INSIDE)
        cls.e_ambiguous = cls._evidence(cls.rel_mixed, b3.id, created_at=INSIDE)
        # rel_accepted: accepted claim -> protected
        cls.rel_accepted = cls._relationship(
            cls.p_new, cls.henry, claim=ClaimStatus.ACCEPTED
        )
        cls._evidence(cls.rel_accepted, b1.id, created_at=INSIDE)

    @classmethod
    def _build_other_group(cls):
        cls.doc_other, other_blocks = cls._document("Synthetic_RST_other.docx", 1)
        cls.group_other = cls._group(GROUP_OTHER, [cls.doc_other.id])
        cls.other_block = other_blocks[0]
        cls.run_other = cls._run(cls.group_other.id, other_blocks)
        cls.other_person = cls._person(SYNTH + " Other Witness")
        cls.other_mention = cls._mention(
            cls.other_block.id, created_at=INSIDE, person=cls.other_person
        )

    @classmethod
    def _build_research_scenario(cls):
        cls.doc_research, research_blocks = cls._document(
            "Synthetic_RST_research.docx", 1
        )
        cls.group_research = cls._group(GROUP_RESEARCH, [cls.doc_research.id])
        cls.research_block = research_blocks[0]
        cls.run_research = cls._run(cls.group_research.id, research_blocks)
        cls.research_mention = cls._mention(cls.research_block.id, created_at=INSIDE)
        session = ResearchSession(
            mode=ResearchMode.RAG,
            question=SYNTH + " who signed?",
            answer="synthetic answer",
            status=ResearchStatus.COMPLETED,
            evidence_sufficient=True,
            sources=[{"block_id": cls.research_block.id, "quote": SYNTH}],
            relationships=[],
            group_id=cls.group_research.id,
            group_name=GROUP_RESEARCH,
        )
        cls.db.add(session)
        cls.db.commit()

    @classmethod
    def _build_noise_group(cls):
        """Evidence that merely coincides with the run window must stay untouched."""
        cls.doc_noise, noise_blocks = cls._document("Synthetic_RST_noise.docx", 2)
        cls.group_noise = cls._group(GROUP_NOISE, [cls.doc_noise.id])
        b_ok, b_untouched = noise_blocks
        cls.noise_ok_block = b_ok
        cls.noise_untouched_block = b_untouched
        # The run only ever saw the first block (no ledger item exists for the second).
        cls.run_noise = cls._run(cls.group_noise.id, [b_ok])
        cls.noise_a = cls._person(SYNTH + " Noise A")
        cls.noise_b = cls._person(SYNTH + " Noise B")
        cls.rel_noise = cls._relationship(cls.noise_a, cls.noise_b)
        # Created *before* the run: a legacy row that the window must not claim.
        cls.e_pre_run = cls._evidence(cls.rel_noise, b_ok.id, created_at=OUTSIDE)
        # Created inside the window, but on a block this run never processed.
        cls.e_never_processed = cls._evidence(
            cls.rel_noise, b_untouched.id, created_at=INSIDE
        )

    @classmethod
    def _build_reference_group(cls):
        """Only an id reference protects a derived record; a block reference does not."""
        cls.doc_refs, ref_blocks = cls._document("Synthetic_RST_refs.docx", 1)
        cls.group_refs = cls._group(GROUP_REFS, [cls.doc_refs.id])
        cls.refs_block = ref_blocks[0]
        cls.run_refs = cls._run(cls.group_refs.id, ref_blocks)
        cls.refs_person = cls._person(SYNTH + " Ref Endpoint")
        cls.refs_other = cls._person(SYNTH + " Ref Free")
        cls.m_referenced = cls._mention(cls.refs_block.id, created_at=INSIDE)
        cls.m_free = cls._mention(cls.refs_block.id, created_at=INSIDE)
        cls.rel_refs = cls._relationship(cls.refs_person, cls.refs_other)
        cls.e_referenced = cls._evidence(cls.rel_refs, cls.refs_block.id, created_at=INSIDE)
        cls.e_free = cls._evidence(cls.rel_refs, cls.refs_block.id, created_at=INSIDE)
        session = ResearchSession(
            mode=ResearchMode.RAG,
            question=SYNTH + " which evidence?",
            answer="synthetic answer",
            status=ResearchStatus.COMPLETED,
            evidence_sufficient=True,
            sources=[
                {
                    "block_id": cls.refs_block.id,
                    "mention_id": cls.m_referenced.id,
                    "evidence_id": cls.e_referenced.id,
                    "quote": SYNTH,
                }
            ],
            relationships=[],
            group_id=cls.group_refs.id,
            group_name=GROUP_REFS,
        )
        cls.db.add(session)
        cls.db.commit()

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
                db.query(KGExtractionResetAudit).filter(
                    KGExtractionResetAudit.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchSession).filter(
                    ResearchSession.group_id.in_(group_ids)
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
                .filter(Document.original_filename.like("Synthetic_RST_%"))
                .all()
            ]
            if doc_ids:
                page_ids = [
                    row[0] for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
                ]
                block_ids = [
                    row[0] for row in db.query(Block.id).filter(Block.page_id.in_(page_ids or [-1])).all()
                ]
                if block_ids:
                    relationship_ids = [
                        row[0]
                        for row in db.query(RelationshipEvidence.relationship_id)
                        .filter(RelationshipEvidence.block_id.in_(block_ids))
                        .distinct()
                        .all()
                    ]
                    db.query(RelationshipEvidence).filter(
                        RelationshipEvidence.block_id.in_(block_ids)
                    ).delete(synchronize_session=False)
                    if relationship_ids:
                        db.query(Relationship).filter(
                            Relationship.id.in_(relationship_ids)
                        ).delete(synchronize_session=False)
                    db.query(PersonMention).filter(
                        PersonMention.block_id.in_(block_ids)
                    ).delete(synchronize_session=False)
                    db.query(Block).filter(Block.id.in_(block_ids)).delete(
                        synchronize_session=False
                    )
                if page_ids:
                    db.query(Page).filter(Page.id.in_(page_ids)).delete(
                        synchronize_session=False
                    )
                db.query(Document).filter(Document.id.in_(doc_ids)).delete(
                    synchronize_session=False
                )
            person_ids = [
                row[0]
                for row in db.query(Person.id)
                .filter(Person.display_name.like(SYNTH + "%"))
                .all()
            ]
            for person_id in person_ids:
                mentioned = (
                    db.query(PersonMention.id)
                    .filter(PersonMention.person_id == person_id)
                    .first()
                )
                related = (
                    db.query(Relationship.id)
                    .filter(
                        (Relationship.source_person_id == person_id)
                        | (Relationship.target_person_id == person_id)
                    )
                    .first()
                )
                if mentioned is None and related is None:
                    db.query(Person).filter(Person.id == person_id).delete(
                        synchronize_session=False
                    )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    def _counts(self):
        return {
            "mentions": self.db.query(PersonMention.id).count(),
            "evidence": self.db.query(RelationshipEvidence.id).count(),
            "relationships": self.db.query(Relationship.id).count(),
            "persons": self.db.query(Person.id).count(),
            "runs": self.db.query(KGExtractionRun.id).count(),
            "items": self.db.query(KGExtractionItem.id).count(),
            "audit": self.db.query(KGExtractionResetAudit.id).count(),
        }

    # ---------------------------------------------------------------- preview
    def test_01_dry_run_writes_nothing(self):
        before = self._counts()
        result = extraction_reset.execute_reset(
            self.db, group_id=self.group_main.id, dry_run=True
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["deleted"], {})
        self.assertEqual(self._counts(), before)
        self.assertEqual(
            self.db.query(KGExtractionResetAudit.id).count(), before["audit"]
        )
        run = self.db.query(KGExtractionRun).filter(
            KGExtractionRun.id == self.run_main.id
        ).one()
        self.assertIsNone(run.reset_at)

    def test_02_preview_classifies_provable_ambiguous_and_protected(self):
        result = extraction_reset.execute_reset(
            self.db, group_id=self.group_main.id, dry_run=True
        )
        counts = result["counts"]
        self.assertEqual(counts["target_runs"], 1)
        # deletable mentions: the two on b1 written inside the window; m_outside is
        # outside the window and m_not_succeeded sits on a failed block.
        self.assertEqual(counts["mentions_deletable"], 2)
        self.assertEqual(counts["mentions_ambiguous"], 2)
        # Evidence is classified independently of the claim it supports: rel_ok's and
        # rel_mixed's in-window evidence are both removable, while the evidence of the
        # accepted claim stays as the trace of that human decision.
        self.assertEqual(counts["evidence_deletable"], 2)
        self.assertEqual(counts["evidence_ambiguous"], 1)
        self.assertEqual(counts["evidence_protected_referenced"], 1)
        protected = {item["id"]: item["reason"] for item in result["relationships_protected"]}
        self.assertEqual(protected[self.rel_accepted.id], "accepted_claim")
        self.assertEqual(protected[self.rel_mixed.id], "has_unprovable_evidence")
        self.assertEqual(result["relationships_partially_cleaned"], [self.rel_mixed.id])
        self.assertEqual(counts["relationships_deletable"], 1)
        protected_persons = {item["id"]: item["reason"] for item in result["persons_protected"]}
        self.assertEqual(
            protected_persons[self.p_shared.id], "still_has_mentions_outside_target_runs"
        )
        # p_new is still an endpoint of the two protected relationships.
        self.assertEqual(protected_persons[self.p_new.id], "still_has_relationships")
        # Henry is never even a candidate: his only link is a protected relationship.
        self.assertNotIn(self.henry.id, result["persons_deletable"])
        self.assertEqual(result["persons_deletable"], [self.p_endpoint.id])
        self.assertEqual(counts["relationships_deletable"], 1)
        self.assertIsNotNone(
            self.db.query(Person).filter(Person.id == self.henry.id).one_or_none()
        )

    def test_03_other_group_is_not_in_the_plan(self):
        result = extraction_reset.execute_reset(
            self.db, group_id=self.group_main.id, dry_run=True
        )
        self.assertEqual(
            [entry["run_id"] for entry in result["target_runs"]], [self.run_main.id]
        )
        self.assertNotIn(self.run_other.id, [e["run_id"] for e in result["target_runs"]])

    # ------------------------------------------------------------------ guards
    def test_04_execution_requires_exact_confirmation_and_reason(self):
        with self.assertRaises(extraction_reset.ResetRefused):
            extraction_reset.execute_reset(
                self.db, group_id=self.group_main.id, dry_run=False, confirm="wrong", reason="x"
            )
        with self.assertRaises(extraction_reset.ResetRefused):
            extraction_reset.execute_reset(
                self.db,
                group_id=self.group_main.id,
                dry_run=False,
                confirm=GROUP_MAIN,
                reason="",
            )

    def test_05_product_database_is_refused(self):
        with mock.patch.object(extraction_reset, "_database_name", return_value="ocr_platform"):
            with self.assertRaises(extraction_reset.ResetRefused):
                extraction_reset.execute_reset(
                    self.db, group_id=self.group_main.id, dry_run=True
                )

    def test_06_block_reference_does_not_protect_derived_records(self):
        detail = extraction_reset.execute_reset(
            self.db, group_id=self.group_research.id, dry_run=True
        )
        # The snapshot cites the archival block, not the derived mention: the mock
        # mention on that block is still provably mock and therefore removable.
        self.assertEqual(detail["counts"]["mentions_deletable"], 1)
        self.assertEqual(detail["counts"]["mentions_ambiguous"], 0)
        self.assertEqual(detail["counts"]["research_blocks_referenced"], 1)
        notes = " ".join(detail["notes"])
        self.assertIn("引用的是原始区块文本", notes)
        # The archival block itself is never a deletion candidate.
        self.assertIsNotNone(
            self.db.query(Block).filter(Block.id == self.research_block.id).one_or_none()
        )

    def test_06a_time_overlap_alone_is_not_provenance(self):
        detail = extraction_reset.execute_reset(
            self.db, group_id=self.group_noise.id, dry_run=True
        )
        counts = detail["counts"]
        # The only in-scope evidence was written *before* the run, and the row written
        # inside the window sits on a block the run never processed.
        self.assertEqual(counts["evidence_deletable"], 0)
        self.assertEqual(counts["evidence_ambiguous"], 1)
        self.assertEqual(counts["relationships_deletable"], 0)
        protected = {item["id"]: item["reason"] for item in detail["relationships_protected"]}
        self.assertEqual(protected[self.rel_noise.id], "has_unprovable_evidence")
        self.assertEqual(detail["relationships_partially_cleaned"], [])
        self.assertIsNotNone(
            self.db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.id == self.e_never_processed.id)
            .one_or_none()
        )

    def test_06b_only_id_references_protect_a_record(self):
        detail = extraction_reset.execute_reset(
            self.db, group_id=self.group_refs.id, dry_run=True
        )
        counts = detail["counts"]
        self.assertEqual(counts["mentions_protected_referenced"], 1)
        self.assertEqual(counts["mentions_deletable"], 1)
        self.assertEqual(counts["evidence_protected_referenced"], 1)
        # Everything else on the same block is removable, and the claim is only kept
        # because one of its evidences is referenced.
        self.assertEqual(counts["evidence_deletable"], 1)
        self.assertEqual(counts["relationships_deletable"], 0)
        self.assertIn(self.m_referenced.id, detail["protected_mentions"])
        self.assertIn(self.e_referenced.id, detail["protected_evidence"])
        self.assertIn(self.m_free.id, detail["mentions_deletable"])
        self.assertIn(self.e_free.id, detail["evidence_deletable"])
        self.assertEqual(counts["research_mentions_referenced"], 1)
        self.assertEqual(counts["research_evidences_referenced"], 1)

    def test_07_unknown_or_already_cleaned_scope_is_reported(self):
        with self.assertRaises(Exception):
            extraction_reset.execute_reset(self.db, group_id=987654, dry_run=True)

    # ---------------------------------------------------------------- execute
    def test_08_execute_removes_only_provable_records_and_keeps_audit(self):
        before = self._counts()
        result = extraction_reset.execute_reset(
            self.db,
            group_id=self.group_main.id,
            dry_run=False,
            confirm=GROUP_MAIN,
            reason="清理 Mock 运行 #%s 的派生记录" % self.run_main.id,
            operator="test",
        )
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["deleted"]["person_mentions"], 2)
        # rel_ok disappears with its evidence; rel_mixed keeps only its unprovable
        # evidence while its mock evidence is removed; the accepted claim keeps its own.
        self.assertEqual(result["deleted"]["relationship_evidences"], 2)
        self.assertEqual(result["deleted"]["relationships"], 1)
        self.assertEqual(result["deleted"]["persons"], 1)  # p_endpoint only

        # Protected rows survive.
        self.assertIsNotNone(
            self.db.query(PersonMention).filter(PersonMention.id == self.m_outside.id).one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(PersonMention)
            .filter(PersonMention.id == self.m_not_succeeded.id)
            .one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(Relationship).filter(Relationship.id == self.rel_accepted.id).one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(Relationship).filter(Relationship.id == self.rel_mixed.id).one_or_none()
        )
        # Evidence of protected relationships is kept, never orphaned.
        self.assertEqual(
            self.db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.relationship_id == self.rel_accepted.id)
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.relationship_id == self.rel_mixed.id)
            .count(),
            1,
        )
        # The claim survives; only its unprovable evidence is left.
        self.assertEqual(
            self.db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.relationship_id == self.rel_mixed.id)
            .one()
            .block_id,
            self.b3.id,
        )
        self.assertIsNotNone(
            self.db.query(Person).filter(Person.id == self.henry.id).one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(Person).filter(Person.id == self.p_shared.id).one_or_none()
        )

        # Run rows and their items are history: never deleted, only stamped.
        run = self.db.query(KGExtractionRun).filter(
            KGExtractionRun.id == self.run_main.id
        ).one()
        self.assertIsNotNone(run.reset_at)
        self.assertIn("Mock", run.reset_reason)
        self.assertEqual(
            self.db.query(KGExtractionItem)
            .filter(KGExtractionItem.run_id == self.run_main.id)
            .count(),
            3,
        )
        # Audit row records what happened.
        audit = (
            self.db.query(KGExtractionResetAudit)
            .filter(KGExtractionResetAudit.group_id == self.group_main.id)
            .order_by(KGExtractionResetAudit.id.desc())
            .first()
        )
        self.assertIsNotNone(audit)
        self.assertFalse(audit.dry_run)
        self.assertEqual(audit.run_ids, [self.run_main.id])
        self.assertEqual(audit.counts["person_mentions"], 2)
        self.assertEqual(audit.operator, "test")

        # Other group untouched.
        after = self._counts()
        self.assertEqual(
            self.db.query(PersonMention).filter(
                PersonMention.id == self.other_mention.id
            ).count(),
            1,
        )
        self.assertGreater(before["mentions"], after["mentions"])

        # A second cleanup finds the run already reset and refuses to invent work.
        with self.assertRaises(extraction_reset.ResetConflict):
            extraction_reset.execute_reset(
                self.db,
                group_id=self.group_main.id,
                dry_run=False,
                confirm=GROUP_MAIN,
                reason="again",
            )

    def test_09_execution_rolls_back_on_error(self):
        # Re-arm the ledger row (test setup only) so the cleanup has work to do again.
        run = self.db.query(KGExtractionRun).filter(
            KGExtractionRun.id == self.run_main.id
        ).one()
        run.reset_at = None
        run.reset_reason = None
        self.db.commit()

        person_a = self._person(SYNTH + " Rollback A")
        person_b = self._person(SYNTH + " Rollback B")
        relationship = self._relationship(person_a, person_b)
        mention = self._mention(self.b1.id, created_at=INSIDE)
        evidence = self._evidence(relationship, self.b1.id, created_at=INSIDE)

        class _Boom(Exception):
            pass

        with mock.patch.object(
            extraction_reset,
            "KGExtractionResetAudit",
            side_effect=_Boom("audit unavailable"),
        ):
            with self.assertRaises(_Boom):
                extraction_reset.execute_reset(
                    self.db,
                    group_id=self.group_main.id,
                    dry_run=False,
                    confirm=GROUP_MAIN,
                    reason="rollback test",
                )
        self.db.expire_all()
        # Nothing was committed: the mention, the evidence and the relationship survive,
        # and the run is not stamped as cleaned.
        self.assertIsNotNone(
            self.db.query(PersonMention).filter(PersonMention.id == mention.id).one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(RelationshipEvidence)
            .filter(RelationshipEvidence.id == evidence.id)
            .one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(Relationship)
            .filter(Relationship.id == relationship.id)
            .one_or_none()
        )
        refreshed = self.db.query(KGExtractionRun).filter(
            KGExtractionRun.id == self.run_main.id
        ).one()
        self.assertIsNone(refreshed.reset_at)

    def test_10_api_preview_and_execution_contract(self):
        preview = self.client.post(
            "/api/kg/groups/%d/extraction/reset" % self.group_other.id,
            json={"dry_run": True},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["database_name"], self.db.get_bind().url.database)
        self.assertIn("counts", body)
        self.assertEqual(body["deleted"], {})

        bad = self.client.post(
            "/api/kg/groups/%d/extraction/reset" % self.group_other.id,
            json={"dry_run": False, "confirm": "nope", "reason": "x"},
        )
        self.assertEqual(bad.status_code, 400, bad.text)

        missing = self.client.post(
            "/api/kg/groups/987654/extraction/reset", json={"dry_run": True}
        )
        self.assertEqual(missing.status_code, 404, missing.text)
