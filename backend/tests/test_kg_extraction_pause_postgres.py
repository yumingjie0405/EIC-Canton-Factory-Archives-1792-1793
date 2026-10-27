"""PostgreSQL test: a manual pause must stop the worker loop.

Regression for a real cost incident: ``POST /extraction/runs/{id}/pause`` wrote
``paused_manual`` to the ledger, but the worker kept iterating and every per-block
commit wrote its own stale ``running`` status back, so the pause was lost and the run
consumed ~2.3 M tokens believing it was still active.
"""
import unittest

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.kg.enums import IdentityStatus
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

SYNTH = "[SYNTH-PAUSE]"
GROUP = SYNTH + " Group"


class _FakeResponse:
    class metrics:  # noqa: N801 - mirrors BlockExtractionResponse
        total_tokens = 10


@requires_postgres_test_db
class TestExtractionPausePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.factory = make_session_factory(cls.engine)
        cls.db = cls.factory()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls.doc = cls._document("Synthetic_PAUSE_six.docx", 6)
        cls.group = cls.service.create_group(GroupCreate(name=GROUP))
        cls.db.add(
            ResearchGroupDocument(group_id=cls.group.id, document_id=cls.doc["document_id"])
        )
        cls.db.commit()

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
        for index in range(block_count):
            cls.db.add(
                Block(
                    page_id=page.id,
                    block_index=index,
                    block_label="paragraph",
                    bbox=[0, 0, 10, 10],
                    ocr_text_raw="%s text %d" % (SYNTH, index),
                )
            )
        cls.db.commit()
        return {"document_id": document.id, "page_id": page.id}

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            groups = [
                row[0]
                for row in db.query(ResearchGroup.id).filter(ResearchGroup.name == GROUP).all()
            ]
            if groups:
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(groups)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(groups)).delete(
                    synchronize_session=False
                )
            docs = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_PAUSE_%"))
                .all()
            ]
            if docs:
                pages = [row[0] for row in db.query(Page.id).filter(Page.document_id.in_(docs))]
                blocks = (
                    [row[0] for row in db.query(Block.id).filter(Block.page_id.in_(pages))]
                    if pages
                    else []
                )
                if blocks:
                    db.query(Block).filter(Block.id.in_(blocks)).delete(
                        synchronize_session=False
                    )
                if pages:
                    db.query(Page).filter(Page.id.in_(pages)).delete(synchronize_session=False)
                db.query(Document).filter(Document.id.in_(docs)).delete(
                    synchronize_session=False
                )
            db.query(Person).filter(Person.display_name.like(SYNTH + "%")).delete(
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

    def test_01_pause_from_another_session_stops_the_loop(self):
        run = runs.create_run(db=self.db, group_id=self.group.id, budget_blocks=50)
        calls = {"count": 0}

        def extractor(db, block_id):
            calls["count"] += 1
            if calls["count"] == 2:
                # The operator pauses through the API, i.e. a different session.
                other = self.factory()
                try:
                    runs.pause_run(other, run.id, reason="operator stop")
                finally:
                    other.close()
            return _FakeResponse()

        snapshot = runs.process_batch(self.db, run.id, limit=6, extractor=extractor)

        self.assertEqual(snapshot["status"], "paused_manual")
        self.assertEqual(calls["count"], 2, "no block may be processed after the pause")
        self.assertEqual(snapshot["processed_blocks"], 2)
        self.assertIsNone(snapshot["finished_at"])
        self.assertIn("operator stop", snapshot["pause_reason"] or "")

    def test_02_finished_run_is_not_overwritten_by_later_batches(self):
        # Fresh document: the previous test already extracted this one's blocks, and the
        # pipeline would (correctly) skip them instead of exercising the loop again.
        document = self._document("Synthetic_PAUSE_second.docx", 4)
        group = self.service.create_group(GroupCreate(name=GROUP + " second"))
        self.db.add(
            ResearchGroupDocument(group_id=group.id, document_id=document["document_id"])
        )
        self.db.commit()
        run = runs.create_run(db=self.db, group_id=group.id, budget_blocks=50)
        snapshot = runs.process_batch(
            self.db, run.id, limit=20, extractor=lambda db, block_id: _FakeResponse()
        )
        self.assertIn(snapshot["status"], ("completed", "partial"))
        again = runs.process_batch(
            self.db, run.id, limit=20, extractor=lambda db, block_id: _FakeResponse()
        )
        self.assertEqual(again["status"], snapshot["status"])
        self.assertEqual(again["processed_blocks"], snapshot["processed_blocks"])


if __name__ == "__main__":
    unittest.main()
