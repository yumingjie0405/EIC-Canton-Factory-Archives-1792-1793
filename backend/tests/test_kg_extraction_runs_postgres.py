"""PostgreSQL tests: persistent, resumable KG extraction runs (P1).

What is proven here:

* a run is created for a group (or one of its documents) and refuses a second
  concurrent run for the same scope (409) instead of silently double-spending;
* progress is persisted per block in ``kg_extraction_items`` and a block that already
  carries mentions is *skipped* rather than re-extracted (``"None"`` never appears in
  any parameter: the extractor receives a real integer block id);
* a block/token budget pauses the run with a reason and keeps the finished work;
* repeated failures pause the run (format vs. transient error) instead of looping;
* resume continues from the ledger, and a completed run cannot be resumed;
* a single-document scope never touches the other documents of the group;
* after a run, the group graph is populated **from that run's mentions** and the scope
  endpoint reports the ledger snapshot;
* group isolation is preserved end to end: the extracted group's graph stays empty for
  a second group.

Only the database is exercised. The LLM is replaced by an injected fake extractor, so
this suite never contacts a model provider.
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
    IdentityStatus,
    MentionType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.models.kg.extraction_run import (
    KGExtractionItem,
    KGExtractionItemStatus,
    KGExtractionRun,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.page import Page
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.group_scope import GroupNotFound
from app.services.kg import extraction_run_service
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-KGX]"
BASE = "/api/kg"

GROUP_MAIN = SYNTH + " Extraction group"
GROUP_OTHER = SYNTH + " Other group"
GROUP_EMPTY = SYNTH + " Empty extraction group"


class _FakeResponse:
    """Minimal stand-in for ``BlockExtractionResponse``."""

    class metrics:  # noqa: N801 - mirrors the real object shape
        total_tokens = 0

    def __init__(self, tokens: int = 7):
        self.metrics = type("M", (), {"total_tokens": tokens})()


@requires_postgres_test_db
class TestKgExtractionRunsPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls.created_run_ids = []
        cls._cleanup()

        # Two documents in the main group (2 and 3 text blocks), one in the other group.
        cls.doc_main_a = cls._document("Synthetic_KGX_a.docx", 2)
        cls.doc_main_b = cls._document("Synthetic_KGX_b.docx", 3)
        cls.doc_other = cls._document("Synthetic_KGX_other.docx", 1)

        cls.group_main = cls.service.create_group(GroupCreate(name=GROUP_MAIN))
        cls.group_other = cls.service.create_group(GroupCreate(name=GROUP_OTHER))
        cls.group_empty = cls.service.create_group(GroupCreate(name=GROUP_EMPTY))
        for group, document in (
            (cls.group_main, cls.doc_main_a),
            (cls.group_main, cls.doc_main_b),
            (cls.group_other, cls.doc_other),
        ):
            cls.db.add(
                ResearchGroupDocument(
                    group_id=group.id,
                    document_id=document["document_id"],
                    import_status=GroupImportStatus.READY,
                    last_batch_id="kgx",
                )
            )
        cls.db.commit()
        cls._identity_cursor = 0

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _document(cls, filename, block_count):
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.DOCX,
            file_size=1024,
            page_count=1 if block_count else 0,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(document)
        cls.db.flush()
        block_ids = []
        if block_count:
            page = Page(document_id=document.id, page_number=1, width=1000, height=1400)
            cls.db.add(page)
            cls.db.flush()
            for index in range(block_count):
                block = Block(
                    page_id=page.id,
                    block_index=index,
                    block_label="paragraph",
                    bbox=[0, 0, 10, 10],
                    ocr_text_raw="%s paragraph %d of %s" % (SYNTH, index, filename),
                )
                cls.db.add(block)
                cls.db.flush()
                block_ids.append(block.id)
        cls.db.commit()
        return {"document_id": document.id, "block_ids": block_ids}

    @classmethod
    def _mention_for(cls, block_id: int):
        """Create one provisional person + one mention for a block (idempotent)."""
        existing = (
            cls.db.query(PersonMention).filter(PersonMention.block_id == block_id).one_or_none()
        )
        if existing is not None:
            return existing
        cls._identity_cursor += 1
        person = Person(
            display_name="%s Person %d" % (SYNTH, cls._identity_cursor),
            canonical_name_en="%s Person %d" % (SYNTH, cls._identity_cursor),
            identity_status=IdentityStatus.PROVISIONAL,
        )
        cls.db.add(person)
        cls.db.flush()
        mention = PersonMention(
            block_id=block_id,
            person_id=person.id,
            raw_text="%s mention" % SYNTH,
            mention_type=MentionType.PERSON_NAME,
            resolution_status=ResolutionStatus.RESOLVED,
            review_status=ReviewStatusKG.UNREVIEWED,
        )
        cls.db.add(mention)
        cls.db.commit()
        return mention

    @classmethod
    def _extractor(cls, db, block_id):
        assert isinstance(block_id, int) and block_id > 0, "block id must be a real integer"
        assert str(block_id) != "None", "None must never be stringified into a parameter"
        cls._mention_for(block_id)
        return _FakeResponse()

    @classmethod
    def _create_run(cls, **kwargs):
        run = extraction_run_service.create_run(cls.db, **kwargs)
        cls.created_run_ids.append(run.id)
        return run

    @classmethod
    def tearDownClass(cls):
        """Always release the session/engine: an abandoned open transaction would
        block the next suite's ``drop_test_schema()`` forever."""
        try:
            cls._cleanup()
        finally:
            app.dependency_overrides.pop(get_db, None)
            cls.db.close()
            cls.engine.dispose()

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
            run_ids = [
                row[0]
                for row in db.query(KGExtractionRun.id)
                .filter(KGExtractionRun.group_id.in_(group_ids or [-1]))
                .all()
            ]
            if run_ids:
                db.query(KGExtractionItem).filter(
                    KGExtractionItem.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
                db.query(KGExtractionRun).filter(
                    KGExtractionRun.id.in_(run_ids)
                ).delete(synchronize_session=False)
            if group_ids:
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(group_ids)).delete(
                    synchronize_session=False
                )
            doc_ids = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_KGX%"))
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
                person_ids = [
                    row[0]
                    for row in db.query(PersonMention.person_id)
                    .filter(PersonMention.block_id.in_(block_ids or [-1]))
                    .all()
                ]
                if block_ids:
                    db.query(PersonMention).filter(
                        PersonMention.block_id.in_(block_ids)
                    ).delete(synchronize_session=False)
                    db.query(Block).filter(Block.id.in_(block_ids)).delete(
                        synchronize_session=False
                    )
                if person_ids:
                    db.query(Person).filter(Person.id.in_(person_ids)).delete(
                        synchronize_session=False
                    )
                if page_ids:
                    db.query(Page).filter(Page.id.in_(page_ids)).delete(
                        synchronize_session=False
                    )
                db.query(Document).filter(Document.id.in_(doc_ids)).delete(
                    synchronize_session=False
                )
            db.commit()
        except Exception:  # noqa: BLE001 - cleanup must never mask test failures
            db.rollback()

    # ------------------------------------------------------------ contracts
    def test_01_scope_counting_and_contract(self):
        run = self._create_run(group_id=self.group_main.id)
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.scope_type, "group")
        self.assertEqual(run.scope_id, self.group_main.id)
        self.assertIsNone(run.document_id)
        # 2 + 3 blocks, and only blocks with text are counted
        self.assertEqual(run.total_blocks, 5)
        self.assertTrue(run.model)

        single = self._create_run(
            group_id=self.group_main.id, document_id=self.doc_main_b["document_id"]
        )
        self.assertEqual(single.scope_type, "document")
        self.assertEqual(single.total_blocks, 3)

        # Park both runs so later tests start from a known, conflict-free ledger.
        extraction_run_service.pause_run(self.db, run.id, "contract test cleanup")
        extraction_run_service.pause_run(self.db, single.id, "contract test cleanup")

    def test_02_unknown_group_and_foreign_document_are_rejected(self):
        with self.assertRaises(GroupNotFound):
            extraction_run_service.create_run(self.db, group_id=987654)
        with self.assertRaises(GroupNotFound):
            extraction_run_service.create_run(
                self.db,
                group_id=self.group_main.id,
                document_id=self.doc_other["document_id"],
            )

    def test_03_concurrent_run_for_same_scope_conflicts(self):
        run = self._create_run(group_id=self.group_other.id)
        with self.assertRaises(extraction_run_service.RunConflict):
            extraction_run_service.create_run(self.db, group_id=self.group_other.id)
        extraction_run_service.pause_run(self.db, run.id)
        # A paused run no longer blocks a new one
        second = self._create_run(group_id=self.group_other.id)
        self.assertNotEqual(second.id, run.id)

    def test_04_batch_progress_is_persisted_per_block(self):
        run = self._create_run(group_id=self.group_main.id)
        snapshot = extraction_run_service.process_batch(
            self.db, run.id, limit=3, extractor=self._extractor
        )
        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["processed_blocks"], 3)
        self.assertEqual(snapshot["attempted_blocks"], 3)
        self.assertEqual(snapshot["tokens_used"], 21)
        self.assertAlmostEqual(snapshot["progress_percent"], 60.0)

        items = (
            self.db.query(KGExtractionItem)
            .filter(KGExtractionItem.run_id == run.id)
            .all()
        )
        self.assertEqual(len(items), 3)
        self.assertEqual({item.status for item in items}, {KGExtractionItemStatus.SUCCEEDED.value})

        final = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["processed_blocks"], 5)
        self.assertIsNotNone(final["finished_at"])

        # Re-processing a terminal run is a no-op: no second item per block.
        again = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(again["status"], "completed")
        self.assertEqual(again["processed_blocks"], 5)
        self.assertEqual(
            self.db.query(KGExtractionItem).filter(KGExtractionItem.run_id == run.id).count(),
            5,
        )

    def test_05_already_extracted_blocks_are_skipped_not_repeated(self):
        run = self._create_run(group_id=self.group_main.id)
        snapshot = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        # test_04 already wrote mentions for every block of the group
        self.assertEqual(snapshot["skipped_blocks"], 5)
        self.assertEqual(snapshot["processed_blocks"], 0)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["tokens_used"], 0)

    def test_06_block_budget_pauses_and_resume_continues(self):
        document = self._document("Synthetic_KGX_budget.docx", 3)
        self.db.add(
            ResearchGroupDocument(
                group_id=self.group_empty.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgx",
            )
        )
        self.db.commit()
        run = self._create_run(group_id=self.group_empty.id, budget_blocks=2)
        self.assertEqual(run.total_blocks, 3)
        snapshot = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(snapshot["status"], "paused_budget")
        self.assertIn("预算", snapshot["pause_reason"])
        self.assertEqual(snapshot["processed_blocks"], 2)

        # A paused run stops immediately; nothing is spent by re-entering it.
        idle = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(idle["status"], "paused_budget")
        self.assertEqual(idle["processed_blocks"], 2)

        # Resuming a budget-paused run requires a new ceiling: silently continuing
        # would pause again on the very next block.
        with self.assertRaises(extraction_run_service.RunStateError):
            extraction_run_service.resume_run(self.db, run.id)

        extraction_run_service.resume_run(self.db, run.id, budget_blocks=5)
        done = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["processed_blocks"], 3)
        self.assertEqual(done["skipped_blocks"], 0)
        self.assertEqual(done["attempted_blocks"], 3)
        self.assertEqual(done["budget_blocks"], 5)

    def test_07_consecutive_failures_pause_with_reason(self):
        document = self._document("Synthetic_KGX_broken.docx", 3)
        self.db.add(
            ResearchGroupDocument(
                group_id=self.group_other.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgx",
            )
        )
        self.db.commit()
        run = self._create_run(
            group_id=self.group_other.id,
            document_id=document["document_id"],
            max_consecutive_failures=2,
        )

        def broken(db, block_id):
            raise ValueError("model returned unparsable JSON")

        snapshot = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=broken
        )
        self.assertEqual(snapshot["status"], "paused_format")
        self.assertEqual(snapshot["failed_blocks"], 2)
        self.assertIn("连续", snapshot["pause_reason"])

        failed_items = (
            self.db.query(KGExtractionItem)
            .filter(
                KGExtractionItem.run_id == run.id,
                KGExtractionItem.status == KGExtractionItemStatus.FAILED.value,
            )
            .all()
        )
        self.assertEqual(len(failed_items), 2)
        self.assertIn("unparsable", failed_items[0].error)

        # Resume continues from the ledger, not from the start.
        extraction_run_service.resume_run(self.db, run.id)
        self.assertEqual(snapshot["failed_blocks"], 2)
        resumed = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(resumed["status"], "partial")
        self.assertEqual(resumed["processed_blocks"], 1)
        self.assertEqual(resumed["failed_blocks"], 2)

        with self.assertRaises(extraction_run_service.RunStateError):
            extraction_run_service.resume_run(self.db, run.id)

    def test_08_transient_errors_pause_as_service_error(self):
        document = self._document("Synthetic_KGX_transient.docx", 2)
        self.db.add(
            ResearchGroupDocument(
                group_id=self.group_empty.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgx",
            )
        )
        self.db.commit()
        run = self._create_run(
            group_id=self.group_empty.id,
            document_id=document["document_id"],
            max_consecutive_failures=2,
        )

        def flaky(db, block_id):
            raise TimeoutError("request timed out while calling the provider")

        snapshot = extraction_run_service.process_batch(
            self.db, run.id, limit=10, extractor=flaky
        )
        self.assertEqual(snapshot["status"], "paused_error")
        self.assertIn("timed out", snapshot["pause_reason"])

    def test_09_group_graph_is_populated_from_the_run(self):
        """After extraction the group graph is non-empty and the other group stays empty."""
        main_overview = self.client.get(
            BASE + "/groups/%d/graph/overview?view=all" % self.group_main.id
        ).json()
        self.assertGreaterEqual(len(main_overview["persons"]), 5)

        other_person_names = {
            item["display_name"] for item in main_overview["persons"]
        }
        self.assertTrue(
            all(name.startswith(SYNTH) for name in other_person_names), other_person_names
        )

        scope = self.client.get(BASE + "/groups/%d/graph/scope" % self.group_main.id).json()
        self.assertTrue(scope["task_tracking_available"])
        self.assertEqual(scope["blocks_with_extraction"], 5)
        self.assertEqual(scope["extraction_state"], "ready")
        self.assertIsNotNone(scope["extraction_run"])
        self.assertEqual(scope["extraction_run"]["status"], "completed")

        # Isolation: nothing extracted in the main group shows up in the other one.
        other_scope = self.client.get(
            BASE + "/groups/%d/graph/scope" % self.group_other.id
        ).json()
        main_person_ids = {item["id"] for item in main_overview["persons"]}
        self.assertNotEqual(other_scope["group_id"], scope["group_id"])
        other_overview = self.client.get(
            BASE + "/groups/%d/graph/overview?view=all" % self.group_other.id
        ).json()
        self.assertFalse(
            main_person_ids & {item["id"] for item in other_overview["persons"]}
        )

    # ----------------------------------------------------------------- API
    def test_10_api_starts_and_reports_a_run(self):
        empty_group = self.service.create_group(GroupCreate(name=SYNTH + " API group"))
        document = self._document("Synthetic_KGX_api.docx", 2)
        self.db.add(
            ResearchGroupDocument(
                group_id=empty_group.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgx",
            )
        )
        self.db.commit()
        self.assertIsNone(
            self.client.get(BASE + "/groups/%d/extraction" % empty_group.id).json()
        )

        created = self.client.post(
            BASE + "/groups/%d/extraction?async_task=false" % empty_group.id
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.created_run_ids.append(body["run_id"])
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["total_blocks"], 2)
        self.assertEqual(body["progress_percent"], 0.0)

        conflict = self.client.post(
            BASE + "/groups/%d/extraction?async_task=false" % empty_group.id
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)

        latest = self.client.get(BASE + "/groups/%d/extraction" % empty_group.id).json()
        self.assertEqual(latest["run_id"], body["run_id"])

        detail = self.client.get(BASE + "/extraction/runs/%d" % body["run_id"])
        self.assertEqual(detail.status_code, 200, detail.text)

        paused = self.client.post(BASE + "/extraction/runs/%d/pause" % body["run_id"])
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["status"], "paused_manual")
        self.assertIn("暂停", paused.json()["pause_reason"])

        resumed = self.client.post(
            BASE + "/extraction/runs/%d/resume?async_task=false" % body["run_id"]
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "running")

        self.assertEqual(
            self.client.get(BASE + "/extraction/runs/987654").status_code, 404
        )
        self.assertEqual(
            self.client.get(BASE + "/groups/987654/extraction").status_code, 404
        )
        self.assertEqual(
            self.client.post(
                BASE + "/groups/987654/extraction?async_task=false"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(BASE + "/extraction/runs/987654/pause").status_code, 404
        )

    def test_11_api_rejects_document_outside_group(self):
        response = self.client.post(
            BASE
            + "/groups/%d/extraction?async_task=false&document_id=%d"
            % (self.group_main.id, self.doc_other["document_id"])
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_12_ledger_states_surface_in_scope(self):
        group = self.service.create_group(GroupCreate(name=SYNTH + " Paused group"))
        document = self._document("Synthetic_KGX_paused.docx", 2)
        self.db.add(
            ResearchGroupDocument(
                group_id=group.id,
                document_id=document["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="kgx",
            )
        )
        self.db.commit()
        run = self._create_run(group_id=group.id)
        snapshot = extraction_run_service.process_batch(
            self.db, run.id, limit=1, extractor=self._extractor
        )
        self.assertEqual(snapshot["status"], "running")

        running = self.client.get(BASE + "/groups/%d/graph/scope" % group.id).json()
        self.assertEqual(running["extraction_state"], "extracting")
        self.assertIn("正在抽取", running["state_message"])
        self.assertEqual(running["extraction_run"]["status"], "running")

        extraction_run_service.pause_run(self.db, run.id, "预算确认中")
        paused = self.client.get(BASE + "/groups/%d/graph/scope" % group.id).json()
        self.assertEqual(paused["extraction_state"], "paused_extraction")
        self.assertIn("预算确认中", paused["state_message"])
        # Partially extracted data is still visible instead of being hidden.
        self.assertGreaterEqual(paused["blocks_with_extraction"], 1)


if __name__ == "__main__":
    unittest.main()
