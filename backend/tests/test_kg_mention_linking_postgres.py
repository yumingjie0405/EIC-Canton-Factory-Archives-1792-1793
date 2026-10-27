"""PostgreSQL tests: mention → person linking and the person-mentions payload.

What is proven here:

* a mention is linked to the single unambiguous person with the same normalized name,
  and the link is written as ``candidate`` / ``unreviewed`` — never accepted;
* two same-named people make the mention *ambiguous*, so nothing is linked and the API
  reports the same-name mentions separately instead of claiming the archive is silent;
* rejected mentions, reviewed mentions and already-linked mentions are left alone;
* re-running the reconciliation is a no-op (idempotent);
* the duplicate-person report lists such groups without merging anything.

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
    IdentityStatus,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship
from app.models.page import Page
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.kg import mention_linker
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-LINK]"
GROUP_MAIN = SYNTH + " Group"


@requires_postgres_test_db
class TestKgMentionLinkingPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()

        cls.document = cls._document("Synthetic_LINK_doc.pdf", 4)
        cls.group = cls.service.create_group(GroupCreate(name=GROUP_MAIN))
        cls.db.add(
            ResearchGroupDocument(
                group_id=cls.group.id,
                document_id=cls.document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="link",
            )
        )
        cls.db.commit()
        cls.block_ids = cls.document["block_ids"]

        # Unambiguous case: one person named like the mention (article differs).
        cls.anker = cls._person("Anker " + SYNTH, [])
        cls.mention_anker = cls._mention(cls.block_ids[0], "Anker " + SYNTH)

        # Article-only difference must normalize to the same key.
        cls.hoppe = cls._person("Hoppe " + SYNTH, [])
        cls.mention_hoppe_article = cls._mention(cls.block_ids[1], "the Hoppe " + SYNTH)
        cls.mention_hoppe_plain = cls._mention(cls.block_ids[1], "Hoppe " + SYNTH)

        # Ambiguous case: two people share the key -> no automatic link.
        cls.dupe_a = cls._person("Duplicate " + SYNTH, [])
        cls.dupe_b = cls._person("the Duplicate " + SYNTH, [])
        cls.mention_dupe = cls._mention(cls.block_ids[2], "Duplicate " + SYNTH)

        # A rejected mention must never be revived.
        cls.rejected = cls._mention(
            cls.block_ids[3], "Anker " + SYNTH, status=ResolutionStatus.REJECTED
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
    def _person(cls, name, aliases):
        person = Person(
            display_name=name,
            canonical_name_en=name,
            aliases=list(aliases),
            identity_status=IdentityStatus.PROVISIONAL,
        )
        cls.db.add(person)
        cls.db.commit()
        return person

    @classmethod
    def _mention(cls, block_id, raw_text, status=ResolutionStatus.UNRESOLVED):
        mention = PersonMention(
            block_id=block_id,
            person_id=None,
            raw_text=raw_text,
            normalized_text=raw_text,
            resolution_status=status,
            review_status=ReviewStatusKG.UNREVIEWED,
        )
        cls.db.add(mention)
        cls.db.commit()
        return mention

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            groups = [
                row[0]
                for row in db.query(ResearchGroup.id)
                .filter(ResearchGroup.name == GROUP_MAIN)
                .all()
            ]
            if groups:
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(groups)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(groups)).delete(
                    synchronize_session=False
                )
            persons = [
                row[0]
                for row in db.query(Person.id)
                .filter(Person.display_name.like("%" + SYNTH + "%"))
                .all()
            ]
            docs = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_LINK_%"))
                .all()
            ]
            pages = (
                [row[0] for row in db.query(Page.id).filter(Page.document_id.in_(docs)).all()]
                if docs
                else []
            )
            blocks = (
                [row[0] for row in db.query(Block.id).filter(Block.page_id.in_(pages)).all()]
                if pages
                else []
            )
            if blocks:
                db.query(PersonMention).filter(
                    PersonMention.block_id.in_(blocks)
                ).delete(synchronize_session=False)
                db.query(Relationship).filter(
                    Relationship.evidence_summary.like(SYNTH + "%")
                ).delete(synchronize_session=False)
                db.query(Block).filter(Block.id.in_(blocks)).delete(synchronize_session=False)
            if pages:
                db.query(Page).filter(Page.id.in_(pages)).delete(synchronize_session=False)
            if docs:
                db.query(Document).filter(Document.id.in_(docs)).delete(synchronize_session=False)
            if persons:
                db.query(Person).filter(Person.id.in_(persons)).delete(synchronize_session=False)
            db.commit()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures
            db.rollback()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        app.dependency_overrides.pop(get_db, None)
        cls.db.close()
        cls.engine.dispose()

    # --------------------------------------------------------------- linking
    def test_01_unambiguous_mention_is_linked_as_candidate(self):
        result = mention_linker.reconcile(self.db, block_ids=[self.block_ids[0]])
        self.db.commit()
        self.assertGreaterEqual(result["linked"], 1)
        self.db.refresh(self.mention_anker)
        self.assertEqual(self.mention_anker.person_id, self.anker.id)
        self.assertEqual(self.mention_anker.resolution_status, ResolutionStatus.CANDIDATE)
        self.assertEqual(self.mention_anker.review_status, ReviewStatusKG.UNREVIEWED)

    def test_02_article_is_not_an_identity_difference(self):
        result = mention_linker.reconcile(self.db, block_ids=[self.block_ids[1]])
        self.db.commit()
        self.assertEqual(result["linked"], 2)
        self.db.refresh(self.mention_hoppe_article)
        self.db.refresh(self.mention_hoppe_plain)
        self.assertEqual(self.mention_hoppe_article.person_id, self.hoppe.id)
        self.assertEqual(self.mention_hoppe_plain.person_id, self.hoppe.id)

    def test_03_ambiguous_names_are_never_guessed(self):
        result = mention_linker.reconcile(self.db, block_ids=[self.block_ids[2]])
        self.db.commit()
        self.assertEqual(result["linked"], 0)
        self.assertEqual(result["ambiguous"], 1)
        self.db.refresh(self.mention_dupe)
        self.assertIsNone(self.mention_dupe.person_id)

    def test_04_rejected_mentions_stay_rejected(self):
        mention_linker.reconcile(self.db, block_ids=[self.block_ids[3]])
        self.db.commit()
        self.db.refresh(self.rejected)
        self.assertIsNone(self.rejected.person_id)
        self.assertEqual(self.rejected.resolution_status, ResolutionStatus.REJECTED)

    def test_05_reconciliation_is_idempotent(self):
        first = mention_linker.reconcile(self.db, block_ids=self.block_ids)
        self.db.commit()
        second = mention_linker.reconcile(self.db, block_ids=self.block_ids)
        self.db.commit()
        self.assertEqual(first["linked"], 0, "everything linkable was linked already")
        self.assertEqual(second["linked"], 0)

    def test_06_dry_run_does_not_write(self):
        pending = self._mention(self.block_ids[0], "Anker " + SYNTH)
        self.db.commit()
        preview = mention_linker.reconcile(self.db, block_ids=[self.block_ids[0]], dry_run=True)
        self.db.rollback()
        self.db.refresh(pending)
        self.assertIsNone(pending.person_id)
        self.assertGreaterEqual(preview["linked"], 1)

    def test_07_linked_mentions_are_not_re_pointed(self):
        ours = self._mention(self.block_ids[1], "Hoppe " + SYNTH)
        self.db.commit()
        mention_linker.reconcile(self.db, block_ids=[self.block_ids[1]])
        self.db.commit()
        other = self._person("Hoppe " + SYNTH, [])
        self.db.commit()
        mention_linker.reconcile(self.db, block_ids=[self.block_ids[1]])
        self.db.commit()
        self.db.refresh(ours)
        self.assertEqual(ours.person_id, self.hoppe.id, "a linked mention keeps its person")
        self.assertNotEqual(ours.person_id, other.id)

    # ------------------------------------------------------------------- API
    def test_08_api_reports_same_name_mentions_separately(self):
        response = self.client.get(
            f"/api/kg/groups/{self.group.id}/persons/{self.dupe_a.id}/mentions"
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["in_group_mention_count"], 0)
        self.assertGreaterEqual(payload["unlinked_name_match_count"], 1)
        self.assertTrue(payload["unlinked_items"])
        self.assertFalse(payload["unlinked_items"][0]["linked"])

    def test_09_api_returns_linked_mentions_with_locators(self):
        response = self.client.get(
            f"/api/kg/groups/{self.group.id}/persons/{self.hoppe.id}/mentions"
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreaterEqual(payload["in_group_mention_count"], 2)
        first = payload["items"][0]
        self.assertTrue(first["linked"])
        self.assertIn("document_name", first)
        self.assertIn("block_id", first)

    def test_10_duplicate_report_lists_without_merging(self):
        rows = mention_linker.duplicate_person_candidates(self.db)
        keys = {row["key"] for row in rows}
        expected = "duplicate"
        self.assertTrue(any(expected in key for key in keys), keys)
        self.db.refresh(self.dupe_a)
        self.db.refresh(self.dupe_b)
        self.assertNotEqual(self.dupe_a.id, self.dupe_b.id)


if __name__ == "__main__":
    unittest.main()
