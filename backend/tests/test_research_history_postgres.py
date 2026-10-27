"""PostgreSQL integration tests for research history persistence.

These tests talk to a **real, ephemeral PostgreSQL instance** (created for this
round and destroyed afterwards), never to the product database:

* the module aborts at import unless an isolated test environment is configured;
* the class is skipped unless ``TEST_DATABASE_URL`` points at an authorized
  PostgreSQL *test* target (``ocr_platform_test[_suffix]``);
* the schema is **not** created here — it is created by the reviewed Alembic
  migration, which is exactly what these tests verify end to end.
"""
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.models.research import ResearchMode, ResearchSession, ResearchStatus
from app.schemas.agent import AgentResearchResponse, AnswerCategorization
from app.schemas.rag import (
    AnswerGeneration,
    CitationIntegrity,
    QueryIntentAnalysis,
    RagEntityReference,
    RagQueryResponse,
    RagRelationshipReference,
    RagSourceEvidence,
)
from app.schemas.research import build_research_detail, build_research_summary
from app.services.research_history_service import (
    MODE_AGENT,
    MODE_RAG,
    ResearchHistoryService,
)
from app.test_support.db_fixtures import make_session_factory, make_test_engine, requires_postgres_test_db

QUOTE = "根据 1792 年 11 月 21 日商馆会议记录，Browne 与 Howqua 议定当季毛织品合同。"


def _source(idx: int) -> RagSourceEvidence:
    return RagSourceEvidence(
        source_id="证据 #%d" % idx,
        document_id=11,
        document_name="Canton Consultations 1792",
        page_number=12,
        block_id=100 + idx,
        quote_text=QUOTE,
        relevance_score=0.87,
        matched_entities=["Howqua", "Henry Browne"],
        evidence_type="primary_archival",
        temporal_coverage="1792–1793年 [经过审定]",
        temporal_provenance="human_verified",
    )


def _rag_response(generation: AnswerGeneration = None) -> RagQueryResponse:
    return RagQueryResponse(
        question="Howqua 与 Browne 的毛织品合同如何议定？",
        answer="双方议定了毛织品合同[证据 #1]。",
        evidence_sufficient=True,
        intent=QueryIntentAnalysis(
            raw_query="Howqua 与 Browne 的毛织品合同如何议定？",
            query_type="relational",
            entities=["Howqua", "Henry Browne"],
            time_filter=1792,
            search_keywords=["woolen", "contract"],
        ),
        entities=[RagEntityReference(id=1, display_name="Howqua", primary_role="Security Merchant")],
        relationships=[
            RagRelationshipReference(
                source_person_name="Howqua",
                target_person_name="Henry Browne",
                relation_type="commercial",
                evidence_quote="settled the woolen contracts",
            )
        ],
        sources=[_source(1)],
        execution_time_ms=123.45,
        generation=generation or AnswerGeneration(mode="model", model_name="test-model"),
    )


def _agent_response() -> AgentResearchResponse:
    return AgentResearchResponse(
        task_id="agent-pg-1",
        question="Howqua 与 Browne 的关系？",
        final_answer="### 档案直接记载\n- 记录[证据 #1]。",
        categorization=AnswerCategorization(archival_facts=["记录[证据 #1]。"]),
        sources=[_source(1)],
        evidence_sufficient=True,
        warnings=["w1"],
        generation=AnswerGeneration(mode="model", model_name="test-model"),
    )


@requires_postgres_test_db
class TestResearchHistoryOnPostgres(unittest.TestCase):
    """End-to-end persistence against the migrated PostgreSQL schema."""

    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine(create_schema=False)
        cls.session_factory = make_session_factory(cls.engine)
        cls.service = ResearchHistoryService()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.db = self.session_factory()

    def tearDown(self):
        self.db.rollback()
        # Keep each test isolated without dropping the table.
        self.db.execute(text("DELETE FROM research_sessions"))
        self.db.commit()
        self.db.close()

    def test_01_table_exists_with_expected_columns(self):
        inspector = inspect(self.engine)
        self.assertTrue(inspector.has_table("research_sessions"))
        actual = {column["name"] for column in inspector.get_columns("research_sessions")}
        expected = {column.name for column in ResearchSession.__table__.columns}
        self.assertEqual(actual, expected)

    def test_02_save_and_read_back_evidence_snapshot(self):
        link = self.service.save_response(self.db, MODE_RAG, _rag_response())
        self.assertTrue(link.saved)
        self.assertIsNotNone(link.record_id)

        record = self.service.get_record(self.db, link.record_id)
        self.assertEqual(record.mode, ResearchMode.RAG)
        self.assertEqual(record.status, ResearchStatus.COMPLETED)
        self.assertEqual(record.answer, "双方议定了毛织品合同[证据 #1]。")
        self.assertEqual(record.question, "Howqua 与 Browne 的毛织品合同如何议定？")

        snapshot = record.sources[0]
        self.assertEqual(snapshot["quote_text"], QUOTE)
        self.assertEqual(snapshot["block_id"], 101)
        self.assertEqual(snapshot["source_id"], "证据 #1")
        self.assertEqual(snapshot["document_name"], "Canton Consultations 1792")
        self.assertEqual(snapshot["page_number"], 12)
        self.assertEqual(snapshot["temporal_provenance"], "human_verified")
        self.assertEqual(record.intent["time_filter"], 1792)
        self.assertEqual(record.entities[0]["display_name"], "Howqua")
        self.assertEqual(record.relationships[0]["relation_type"], "commercial")

        # The raw stored values must be the lower-case enum *values*.
        raw = self.db.execute(
            text("select mode::text, status::text, jsonb_array_length(sources::jsonb) from research_sessions where id = :id"),
            {"id": link.record_id},
        ).one()
        self.assertEqual(raw[0], "rag")
        self.assertEqual(raw[1], "completed")
        self.assertEqual(raw[2], 1)

    def test_03_enum_and_datetime_roundtrip(self):
        link = self.service.save_response(self.db, MODE_AGENT, _agent_response())
        record = self.service.get_record(self.db, link.record_id)
        self.assertIsInstance(record.mode, ResearchMode)
        self.assertIsInstance(record.status, ResearchStatus)
        self.assertIsInstance(record.created_at, datetime)
        self.assertIsNone(record.created_at.tzinfo)

    def test_04_citation_failure_is_stored_as_degraded(self):
        generation = AnswerGeneration(
            mode="deterministic_fallback",
            degraded=True,
            failure_reason="authentication_failed",
            failure_message="模型服务鉴权失败",
            citation_check=CitationIntegrity(valid=False, invalid_ids=[9], available_count=1),
        )
        link = self.service.save_response(self.db, MODE_RAG, _rag_response(generation))
        record = self.service.get_record(self.db, link.record_id)
        self.assertEqual(record.status, ResearchStatus.DEGRADED)
        self.assertFalse(record.citation_valid)
        self.assertEqual(record.citation_invalid_ids, [9])

        detail = build_research_detail(record)
        self.assertTrue(detail.generation.degraded)
        self.assertEqual(detail.generation.citation_check.invalid_ids, [9])

    def test_05_pagination_and_mode_filter(self):
        for _ in range(3):
            self.service.save_response(self.db, MODE_RAG, _rag_response())
        self.service.save_response(self.db, MODE_AGENT, _agent_response())

        page1, total = self.service.list_records(self.db, page=1, page_size=3)
        self.assertEqual(total, 4)
        self.assertEqual(len(page1), 3)
        page2, _ = self.service.list_records(self.db, page=2, page_size=3)
        self.assertEqual(len(page2), 1)
        ids = [row.id for row in page1 + page2]
        self.assertEqual(ids, sorted(ids, reverse=True))

        rag_rows, rag_total = self.service.list_records(self.db, mode=MODE_RAG)
        self.assertEqual(rag_total, 3)
        self.assertTrue(all(row.mode == ResearchMode.RAG for row in rag_rows))

    def test_06_detail_does_not_rerun_retrieval_or_model(self):
        from app.services.kg.llm_client import kg_llm_client
        from app.services.rag import rag_service as rag_singleton

        link = self.service.save_response(self.db, MODE_RAG, _rag_response())
        record = self.service.get_record(self.db, link.record_id)

        with mock.patch.object(
            rag_singleton, "answer_question", side_effect=AssertionError("history must not re-run RAG")
        ), mock.patch.object(
            kg_llm_client, "call_extraction_llm", side_effect=AssertionError("history must not call the model")
        ):
            detail = build_research_detail(record)

        self.assertEqual(detail.sources[0].quote_text, QUOTE)
        self.assertEqual(detail.answer, record.answer)

    def test_07_history_api_list_and_detail(self):
        from app.api.routers import research as research_router

        link = self.service.save_response(self.db, MODE_RAG, _rag_response())

        listing = research_router.list_research_history(page=1, page_size=10, mode=None, db=self.db)
        self.assertGreaterEqual(listing.total, 1)
        row = next(item for item in listing.items if item.id == link.record_id)
        self.assertEqual(row.mode, "rag")
        self.assertEqual(row.status, "completed")
        self.assertEqual(row.source_count, 1)

        detail = research_router.get_research_record(record_id=link.record_id, db=self.db)
        self.assertEqual(detail.id, link.record_id)
        self.assertEqual(detail.sources[0].block_id, 101)

    def test_08_missing_record_is_404_without_fake_data(self):
        from fastapi import HTTPException
        from app.api.routers import research as research_router

        self.assertIsNone(self.service.get_record(self.db, 987654))
        with self.assertRaises(HTTPException) as ctx:
            research_router.get_research_record(record_id=987654, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_09_write_failure_is_not_reported_as_saved(self):
        """A real constraint violation must surface as saved=False."""
        from app.repositories.research_repository import ResearchRepository

        self.service.save_response(self.db, MODE_RAG, _rag_response())
        first = self.db.execute(text("select id from research_sessions")).scalar()

        duplicate = ResearchSession(
            id=first, mode=ResearchMode.RAG, question="dup", answer="dup",
            status=ResearchStatus.COMPLETED, evidence_sufficient=True, sources=[],
        )
        # A duplicate primary key must raise through the repository ...
        with self.assertRaises(IntegrityError):
            ResearchRepository(self.db).add(duplicate)
        self.db.rollback()

        # ... and the service must turn that failure into saved=False.
        with mock.patch.object(
            ResearchRepository, "add", side_effect=IntegrityError("INSERT", {}, Exception("duplicate key"))
        ):
            link = self.service.save_response(self.db, MODE_RAG, _rag_response())
        self.assertFalse(link.saved)
        self.assertIsNone(link.record_id)
        self.assertIn("未能写入数据库", link.error)

        # The already-saved row is untouched and no extra row appeared.
        count = self.db.execute(text("select count(*) from research_sessions")).scalar()
        self.assertEqual(count, 1)

    def test_10_repeated_saves_do_not_modify_earlier_records(self):
        first = self.service.save_response(self.db, MODE_RAG, _rag_response())
        original = self.service.get_record(self.db, first.record_id)
        original_answer = original.answer
        original_created = original.created_at

        for _ in range(2):
            self.service.save_response(self.db, MODE_AGENT, _agent_response())

        reloaded = self.service.get_record(self.db, first.record_id)
        self.assertEqual(reloaded.answer, original_answer)
        self.assertEqual(reloaded.created_at, original_created)
        self.assertEqual(reloaded.mode, ResearchMode.RAG)

        summaries = [build_research_summary(row) for row in self.service.list_records(self.db)[0]]
        self.assertEqual(len(summaries), 3)
        self.assertEqual(len({item.id for item in summaries}), 3)


    def test_11_app_startup_does_not_touch_saved_history(self):
        """Mimic the startup ``create_all`` and prove saved rows survive it."""
        from app import main as main_module
        from app.database import Base

        link = self.service.save_response(self.db, MODE_RAG, _rag_response())
        before = self.db.execute(text("select count(*) from research_sessions")).scalar()

        # Exactly what app.main.lifespan does, minus the engine construction.
        Base.metadata.create_all(bind=self.engine, tables=main_module._startup_tables())

        after = self.db.execute(text("select count(*) from research_sessions")).scalar()
        self.assertEqual(before, after)
        self.assertNotIn(
            "research_sessions", {table.name for table in main_module._startup_tables()}
        )
        record = self.service.get_record(self.db, link.record_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.sources[0]["block_id"], 101)

    def test_12_repeated_reads_do_not_mutate_records(self):
        link = self.service.save_response(self.db, MODE_RAG, _rag_response())
        first = self.service.get_record(self.db, link.record_id)
        snapshot = (first.answer, first.question, tuple(first.created_at.timetuple()))

        for _ in range(3):
            detail = build_research_detail(self.service.get_record(self.db, link.record_id))
            self.assertEqual(detail.sources[0].quote_text, QUOTE)

        again = self.service.get_record(self.db, link.record_id)
        self.assertEqual(
            (again.answer, again.question, tuple(again.created_at.timetuple())), snapshot
        )
        self.assertEqual(
            self.db.execute(text("select count(*) from research_sessions")).scalar(), 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
