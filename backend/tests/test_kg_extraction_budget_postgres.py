"""PostgreSQL tests: extraction budget protection and multi-run status reporting.

What is proven
--------------
* no run can be created without a ceiling — a missing budget falls back to the
  deployment default, and an illegal or oversized one is refused instead of clamped;
* processing stops at the ceiling, and usage counters are never reset by pause/resume;
* resuming a budget pause requires a *new*, larger, in-range ceiling;
* a legacy row with ``budget_blocks IS NULL`` is refused by the worker instead of running
  forever;
* repeated/concurrent submissions cannot start a second active run for the same scope;
* run history is classified (active / paused / last finished), so a paused attempt does
  not mask a finished run — in the API and in the group scope payload.
"""
import unittest
from io import BytesIO
from unittest import mock

from docx import Document as DocxDocument
from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document
from app.models.kg.enums import IdentityStatus, MentionType, ResolutionStatus, ReviewStatusKG
from app.models.kg.extraction_run import KGExtractionItem, KGExtractionRun
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.page import Page
from app.models.research_group import ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.kg import extraction_run_service as runs
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-BUD]"
GROUP_A = SYNTH + " Budget group"


class _FakeResponse:
    class metrics:  # noqa: N801 - mirrors BlockExtractionResponse
        total_tokens = 10

    def __init__(self):
        self.metrics = type("M", (), {"total_tokens": 10})()


@requires_postgres_test_db
class TestExtractionBudgetPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls.doc = cls._document("Synthetic_BUD_five.docx", 5)
        cls.group = cls.service.create_group(GroupCreate(name=GROUP_A))
        cls.db.add(
            ResearchGroupDocument(
                group_id=cls.group.id, document_id=cls.doc["document_id"]
            )
        )
        cls.db.commit()
        cls.created_run_ids = []

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
            file_type=__import__(
                "app.models.document", fromlist=["DocumentType"]
            ).DocumentType.DOCX,
            file_size=1024,
            page_count=1,
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
                ocr_text_raw="%s block %d" % (SYNTH, index),
            )
            cls.db.add(block)
            cls.db.flush()
            block_ids.append(block.id)
        cls.db.commit()
        return {"document_id": document.id, "block_ids": block_ids}

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
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(group_ids)).delete(
                    synchronize_session=False
                )
            doc_ids = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_BUD_%"))
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
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    def _extractor(self, db, block_id):
        person = (
            db.query(Person)
            .filter(Person.display_name == SYNTH + " Witness")
            .one_or_none()
        )
        if person is None:
            person = Person(
                display_name=SYNTH + " Witness",
                canonical_name_en=SYNTH + " Witness",
                identity_status=IdentityStatus.PROVISIONAL,
            )
            db.add(person)
            db.flush()
        db.add(
            PersonMention(
                block_id=block_id,
                person_id=person.id,
                raw_text=SYNTH + " mention",
                mention_type=MentionType.PERSON_NAME,
                resolution_status=ResolutionStatus.RESOLVED,
                review_status=ReviewStatusKG.UNREVIEWED,
            )
        )
        db.commit()
        return _FakeResponse()

    def _new_run(self, **kwargs):
        run = runs.create_run(db=self.db, group_id=self.group.id, **kwargs)
        type(self).created_run_ids.append(run.id)
        return run

    def _purge_runs(self):
        """Reset the test domain: runs, their items, mentions and the witness person.

        Mentions survive a run deletion, and a block that already has a mention is
        skipped by design — leaving them would make every later test see "skipped"
        instead of "processed" and hide real behaviour.
        """
        run_ids = [
            run.id
            for run in self.db.query(KGExtractionRun).filter(
                KGExtractionRun.group_id == self.group.id
            )
        ]
        if run_ids:
            self.db.query(KGExtractionItem).filter(
                KGExtractionItem.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            self.db.query(KGExtractionRun).filter(
                KGExtractionRun.id.in_(run_ids)
            ).delete(synchronize_session=False)
        self.db.query(PersonMention).filter(
            PersonMention.block_id.in_(self.doc["block_ids"])
        ).delete(synchronize_session=False)
        witness = (
            self.db.query(Person)
            .filter(Person.display_name == SYNTH + " Witness")
            .one_or_none()
        )
        if witness is not None:
            remaining = (
                self.db.query(PersonMention.id)
                .filter(PersonMention.person_id == witness.id)
                .first()
            )
            if remaining is None:
                self.db.query(Person).filter(Person.id == witness.id).delete(
                    synchronize_session=False
                )
        self.db.commit()

    # ---------------------------------------------------------------- budget
    def test_01_missing_budget_uses_the_conservative_default(self):
        self._purge_runs()
        run = self._new_run()
        self.assertEqual(run.budget_blocks, settings.kg_extraction_default_budget_blocks)
        self.assertEqual(run.budget_source, "default")
        self.assertIsNotNone(run.budget_blocks)

    def test_02_illegal_budgets_are_refused_never_clamped(self):
        self._purge_runs()
        with self.assertRaises(runs.BudgetError):
            self._new_run(budget_blocks=0)
        with self.assertRaises(runs.BudgetError):
            self._new_run(budget_blocks=-3)
        with self.assertRaises(runs.BudgetError):
            self._new_run(budget_blocks=True)
        with self.assertRaises(runs.BudgetError):
            self._new_run(budget_blocks=settings.kg_extraction_max_budget_blocks + 1)
        with self.assertRaises(runs.BudgetError):
            self._new_run(budget_blocks=5, budget_tokens=-1)
        self.assertEqual(
            self.db.query(KGExtractionRun)
            .filter(KGExtractionRun.group_id == self.group.id)
            .count(),
            0,
        )

    def test_03_api_refuses_an_oversized_budget(self):
        self._purge_runs()
        response = self.client.post(
            "/api/kg/groups/%d/extraction?async_task=false&budget_blocks=%d"
            % (self.group.id, settings.kg_extraction_max_budget_blocks + 10)
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("上限", response.json()["detail"])

    def test_04_processing_stops_at_the_ceiling(self):
        self._purge_runs()
        run = self._new_run(budget_blocks=2)
        snapshot = runs.process_batch(self.db, run.id, limit=10, extractor=self._extractor)
        self.assertEqual(snapshot["status"], "paused_budget")
        self.assertEqual(snapshot["attempted_blocks"], 2)
        self.assertEqual(snapshot["tokens_used"], 20)
        self.assertIn("预算", snapshot["pause_reason"])

    def test_05_resume_needs_a_new_larger_in_range_ceiling_and_keeps_usage(self):
        self._purge_runs()
        run = self._new_run(budget_blocks=2)
        runs.process_batch(self.db, run.id, limit=10, extractor=self._extractor)
        with self.assertRaises(runs.RunStateError):
            runs.resume_run(self.db, run.id)
        with self.assertRaises(runs.RunStateError):
            runs.resume_run(self.db, run.id, budget_blocks=2)  # not > attempted
        with self.assertRaises(runs.RunStateError):
            runs.resume_run(
                self.db,
                run.id,
                budget_blocks=settings.kg_extraction_max_budget_blocks + 1,
            )
        resumed = runs.resume_run(self.db, run.id, budget_blocks=4)
        self.assertEqual(resumed["attempted_blocks"], 2)  # usage is cumulative
        self.assertEqual(resumed["budget_blocks"], 4)
        final = runs.process_batch(self.db, run.id, limit=10, extractor=self._extractor)
        self.assertEqual(final["status"], "paused_budget")
        self.assertEqual(final["attempted_blocks"], 4)
        self.assertEqual(final["tokens_used"], 40)

    def test_06_worker_refuses_a_run_without_a_ceiling(self):
        self._purge_runs()
        run = self._new_run(budget_blocks=10)
        run.budget_blocks = None  # simulate a legacy row created before the guard
        self.db.commit()
        snapshot = runs.run_until_stopped(self.db, run.id, batch_size=5)
        self.assertEqual(snapshot["status"], "paused_budget")
        self.assertIn("缺少区块预算", snapshot["pause_reason"])

    def test_07_repeated_submission_cannot_start_a_second_active_run(self):
        self._purge_runs()
        first = self._new_run(budget_blocks=5)
        with self.assertRaises(runs.RunConflict):
            runs.create_run(self.db, group_id=self.group.id)
        response = self.client.post(
            "/api/kg/groups/%d/extraction?async_task=false&budget_blocks=5" % self.group.id
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            self.db.query(KGExtractionRun)
            .filter(
                KGExtractionRun.group_id == self.group.id,
                KGExtractionRun.status.in_(("queued", "running")),
            )
            .count(),
            1,
        )
        del first

    def test_08_processing_twice_does_not_duplicate_mentions(self):
        self._purge_runs()
        run = self._new_run(budget_blocks=10)
        first = runs.process_batch(self.db, run.id, limit=10, extractor=self._extractor)
        self.assertEqual(first["processed_blocks"], 5)
        mentions_before = self.db.query(PersonMention.id).count()
        again = runs.process_batch(self.db, run.id, limit=10, extractor=self._extractor)
        # The run already completed every block, so nothing is written again.
        self.assertEqual(again["status"], "completed")
        self.assertEqual(self.db.query(PersonMention.id).count(), mentions_before)

    # ----------------------------------------------------------- run history
    def test_09_run_roles_are_classified_and_paused_does_not_mask_finished(self):
        self._purge_runs()
        finished = self._new_run(budget_blocks=10)
        runs.process_batch(self.db, finished.id, limit=10, extractor=self._extractor)
        paused = self._new_run(budget_blocks=2)
        runs.pause_run(self.db, paused.id, "人工暂停用于测试")

        summary = self.client.get(
            "/api/kg/groups/%d/extraction/runs" % self.group.id
        ).json()
        self.assertEqual(summary["last_finished_run"]["run_id"], finished.id)
        self.assertEqual(summary["paused_run"]["run_id"], paused.id)
        self.assertIsNone(summary["active_run"])
        self.assertEqual(summary["primary_run"]["run_id"], paused.id)
        self.assertGreaterEqual(len(summary["recent_runs"]), 2)

        scope = self.client.get("/api/kg/groups/%d/graph/scope" % self.group.id).json()
        self.assertIn("last_finished_run", scope["extraction_runs"])
        self.assertEqual(scope["extraction_runs"]["last_finished_run"]["run_id"], finished.id)
        # The group already has results: the paused attempt must not turn the whole
        # group into "paused" nor hide the finished run.
        self.assertNotEqual(scope["extraction_state"], "paused_extraction")
        self.assertIn(str(paused.id), scope["state_message"])
        self.assertIn(str(finished.id), scope["state_message"])

    def test_10a_a_new_model_reextracts_while_the_same_model_skips(self):
        """Idempotency is per model/prompt, not "some mention exists".

        Without this, changing the model silently reused results the new configuration
        never produced (and a block already touched by an old run could never be
        re-extracted at all).
        """
        self._purge_runs()
        first = self._new_run(budget_blocks=10, model="stub-a")
        first_outcome = runs.process_batch(
            self.db, first.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(first_outcome["processed_blocks"], 5)

        same_model = self._new_run(budget_blocks=10, model="stub-a")
        outcome = runs.process_batch(
            self.db, same_model.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(outcome["processed_blocks"], 0)
        self.assertEqual(outcome["skipped_blocks"], 5)

        other_model = self._new_run(budget_blocks=10, model="stub-b")
        outcome_b = runs.process_batch(
            self.db, other_model.id, limit=10, extractor=self._extractor
        )
        self.assertEqual(outcome_b["processed_blocks"], 5)
        self.assertEqual(outcome_b["skipped_blocks"], 0)

    def test_10b_prompt_change_also_invalidates_the_previous_result(self):
        self._purge_runs()
        base = self._new_run(budget_blocks=10, model="stub-a", prompt_hash="p1")
        runs.process_batch(self.db, base.id, limit=10, extractor=self._extractor)

        same = self._new_run(budget_blocks=10, model="stub-a", prompt_hash="p1")
        self.assertEqual(
            runs.process_batch(self.db, same.id, limit=10, extractor=self._extractor)[
                "skipped_blocks"
            ],
            5,
        )

        changed = self._new_run(budget_blocks=10, model="stub-a", prompt_hash="p2")
        self.assertEqual(
            runs.process_batch(self.db, changed.id, limit=10, extractor=self._extractor)[
                "processed_blocks"
            ],
            5,
        )

    def test_10c_unledgered_mentions_are_never_rebilled(self):
        """A block whose mentions predate the ledger cannot be attributed: skip it."""
        self._purge_runs()
        # Mentions only, no run/items at all.
        self._extractor(self.db, self.doc["block_ids"][0])
        run = self._new_run(budget_blocks=10, model="stub-a")
        outcome = runs.process_batch(self.db, run.id, limit=10, extractor=self._extractor)
        self.assertEqual(outcome["skipped_blocks"], 1)
        self.assertEqual(outcome["processed_blocks"], 4)

    def test_10_budget_source_is_recorded(self):
        self._purge_runs()
        explicit = self._new_run(budget_blocks=7)
        self.assertEqual(explicit.budget_source, "explicit")
        self.assertEqual(explicit.budget_blocks, 7)
        runs.pause_run(self.db, explicit.id, "done")
        auto = self._new_run(budget_blocks=9, trigger="auto_import", budget_source="auto")
        self.assertEqual(auto.budget_source, "auto")
