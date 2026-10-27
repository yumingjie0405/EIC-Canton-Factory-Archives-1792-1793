"""Tests for research history persistence, the history API and the save wiring.

No product database and no network are touched:

* schema/repository behaviour runs against a **fresh in-memory SQLite** database
  created by the test itself (never the configured product DSN);
* API tests use stub sessions and patched services.
"""
import os
import unittest
from unittest import mock

from sqlalchemy.exc import OperationalError

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import Base
from app.models.research import ResearchMode, ResearchSession, ResearchStatus
from app.repositories.research_repository import ResearchRepository
from app.test_support.db_fixtures import make_session_factory, make_test_engine
from app.schemas.agent import AgentResearchResponse, AnswerCategorization
from app.schemas.rag import (
    AnswerGeneration,
    CitationIntegrity,
    HistoryLink,
    QueryIntentAnalysis,
    RagEntityReference,
    RagQueryResponse,
    RagRelationshipReference,
    RagSourceEvidence,
)
from app.schemas.research import (
    build_research_detail,
    build_research_summary,
)
from app.services import research_history_service as history_module
from app.services.research_history_service import (
    MODE_AGENT,
    MODE_RAG,
    ResearchHistoryService,
    build_record,
    derive_status,
)


def _source(idx: int, quote: str = "根据商馆会议记录，Browne 向 Howqua 支付货款。") -> RagSourceEvidence:
    return RagSourceEvidence(
        source_id="证据 #%d" % idx,
        document_id=11,
        document_name="Canton Consultations 1792",
        page_number=12,
        block_id=100 + idx,
        quote_text=quote,
        relevance_score=0.87,
        matched_entities=["Howqua"],
        evidence_type="primary_archival",
        temporal_coverage="1792–1793年 [经过审定]",
        temporal_provenance="human_verified",
    )


def _entity() -> RagEntityReference:
    return RagEntityReference(
        id=1,
        display_name="Howqua",
        primary_role="Security Merchant",
        canonical_name_zh="伍秉鉴",
        identity_status="accepted",
    )


def _relationship() -> RagRelationshipReference:
    return RagRelationshipReference(
        source_person_name="Howqua",
        target_person_name="Henry Browne",
        relation_type="commercial",
        evidence_quote="settled the woolen contracts",
        confidence=0.9,
    )


def _rag_response(
    *,
    generation: AnswerGeneration = None,
    evidence_sufficient: bool = True,
    sources=None,
) -> RagQueryResponse:
    return RagQueryResponse(
        question="Howqua 与 Browne 的毛织品合同如何议定？",
        answer="根据商馆会议记录，双方达成了毛织品合同[证据 #1]。",
        evidence_sufficient=evidence_sufficient,
        intent=QueryIntentAnalysis(
            raw_query="Howqua 与 Browne 的毛织品合同如何议定？",
            query_type="relational",
            entities=["Howqua", "Henry Browne"],
            time_filter=1792,
            search_keywords=["woolen", "contract"],
        ),
        entities=[_entity()],
        relationships=[_relationship()],
        sources=[_source(1)] if sources is None else sources,
        execution_time_ms=123.45,
        generation=generation or AnswerGeneration(mode="model", model_name="test-model"),
    )


def _agent_response(*, generation: AnswerGeneration = None, evidence_sufficient: bool = True) -> AgentResearchResponse:
    return AgentResearchResponse(
        task_id="agent-1",
        question="Howqua 与 Browne 的关系？",
        final_answer="### 档案直接记载\n- 记录[证据 #1]。",
        categorization=AnswerCategorization(archival_facts=["记录[证据 #1]。"]),
        sources=[_source(1)],
        entities=[_entity()],
        relationships=[_relationship()],
        evidence_sufficient=evidence_sufficient,
        warnings=["w1"],
        generation=generation or AnswerGeneration(mode="model", model_name="test-model"),
    )


class TestDeriveStatus(unittest.TestCase):
    """A stored status must never be more optimistic than the run that produced it."""

    def test_model_answer_is_completed(self):
        self.assertEqual(derive_status(_rag_response()), ResearchStatus.COMPLETED)

    def test_degraded_generation_is_degraded(self):
        degraded = AnswerGeneration(
            mode="deterministic_fallback",
            degraded=True,
            failure_reason="authentication_failed",
        )
        self.assertEqual(derive_status(_rag_response(generation=degraded)), ResearchStatus.DEGRADED)

    def test_citation_violation_is_not_completed(self):
        generation = AnswerGeneration(
            mode="model",
            degraded=True,
            citation_check=CitationIntegrity(valid=False, invalid_ids=[9], available_count=1),
        )
        self.assertEqual(derive_status(_rag_response(generation=generation)), ResearchStatus.DEGRADED)

    def test_refusal_is_insufficient_evidence(self):
        generation = AnswerGeneration(mode="refused_no_evidence")
        self.assertEqual(
            derive_status(_rag_response(generation=generation, evidence_sufficient=False, sources=[])),
            ResearchStatus.INSUFFICIENT_EVIDENCE,
        )

    def test_insufficient_flag_wins_even_without_generation(self):
        self.assertEqual(
            derive_status(_rag_response(evidence_sufficient=False, sources=[])),
            ResearchStatus.INSUFFICIENT_EVIDENCE,
        )


class TestBuildRecord(unittest.TestCase):
    """The stored record must keep the whole evidence snapshot, not just the text."""

    def test_rag_record_fields(self):
        record = build_record(MODE_RAG, _rag_response())
        self.assertEqual(record.mode, ResearchMode.RAG)
        self.assertEqual(record.status, ResearchStatus.COMPLETED)
        self.assertTrue(record.evidence_sufficient)
        self.assertEqual(record.generation_mode, "model")
        self.assertEqual(record.model_name, "test-model")
        self.assertEqual(record.question, "Howqua 与 Browne 的毛织品合同如何议定？")
        self.assertIn("毛织品合同", record.answer)
        self.assertEqual(record.elapsed_ms, 123.45)

    def test_evidence_snapshot_is_complete(self):
        record = build_record(MODE_RAG, _rag_response())
        self.assertEqual(len(record.sources), 1)
        saved = record.sources[0]
        for field, expected in (
            ("source_id", "证据 #1"),
            ("document_id", 11),
            ("document_name", "Canton Consultations 1792"),
            ("page_number", 12),
            ("block_id", 101),
            ("quote_text", "根据商馆会议记录，Browne 向 Howqua 支付货款。"),
            ("evidence_type", "primary_archival"),
            ("temporal_provenance", "human_verified"),
        ):
            self.assertEqual(saved[field], expected, field)
        self.assertEqual(record.entities[0]["display_name"], "Howqua")
        self.assertEqual(record.relationships[0]["relation_type"], "commercial")
        self.assertEqual(record.intent["time_filter"], 1792)

    def test_citation_check_is_preserved(self):
        generation = AnswerGeneration(
            mode="model",
            degraded=True,
            citation_check=CitationIntegrity(valid=False, invalid_ids=[9, 4], available_count=1),
        )
        record = build_record(MODE_RAG, _rag_response(generation=generation))
        self.assertFalse(record.citation_valid)
        self.assertEqual(record.citation_invalid_ids, [9, 4])
        self.assertEqual(record.citation_available_count, 1)

    def test_agent_record_uses_final_answer_and_budget(self):
        response = _agent_response()
        response.budget_used.elapsed_ms = 987.6
        record = build_record(MODE_AGENT, response)
        self.assertEqual(record.mode, ResearchMode.AGENT)
        self.assertIn("档案直接记载", record.answer)
        self.assertEqual(record.elapsed_ms, 987.6)
        self.assertEqual(record.warnings, ["w1"])

    def test_question_override_and_truncation(self):
        record = build_record(MODE_RAG, _rag_response(), question="问" * 5000)
        self.assertEqual(len(record.question), history_module._MAX_QUESTION_CHARS)


class TestPersistenceAgainstInMemorySqlite(unittest.TestCase):
    """Real INSERT/SELECT/ORDER BY/OFFSET logic, on an isolated in-memory database.

    The engine comes from :mod:`app.test_support.db_fixtures` (never
    ``create_engine`` directly), so the test cannot reach the product DSN.
    """

    _ENV_KEYS = ("EIC_TEST_MODE", "TEST_DATABASE_URL")
    _previous_env = {}

    def setUp(self):
        # The isolation bootstrap already guarantees a test-mode sqlite target;
        # nothing here may reach the product DSN.
        self._previous_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.engine = make_test_engine(create_schema=False)
        Base.metadata.create_all(bind=self.engine, tables=[ResearchSession.__table__])
        self.db = make_session_factory(self.engine)()
        self.service = ResearchHistoryService()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_save_then_read_back(self):
        link = self.service.save_response(self.db, MODE_RAG, _rag_response())
        self.assertTrue(link.saved)
        self.assertIsNotNone(link.record_id)

        record = self.service.get_record(self.db, link.record_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, ResearchStatus.COMPLETED)
        self.assertEqual(len(record.sources), 1)
        self.assertEqual(record.sources[0]["block_id"], 101)

    def test_list_is_newest_first_with_pagination(self):
        for i in range(5):
            self.service.save_response(self.db, MODE_RAG, _rag_response())
        self.service.save_response(self.db, MODE_AGENT, _agent_response())

        page1, total = self.service.list_records(self.db, page=1, page_size=4)
        self.assertEqual(total, 6)
        self.assertEqual(len(page1), 4)
        page2, _ = self.service.list_records(self.db, page=2, page_size=4)
        self.assertEqual(len(page2), 2)
        ids = [r.id for r in page1 + page2]
        self.assertEqual(ids, sorted(ids, reverse=True), "history must be newest first")

    def test_mode_filter(self):
        self.service.save_response(self.db, MODE_RAG, _rag_response())
        self.service.save_response(self.db, MODE_AGENT, _agent_response())
        rag_rows, rag_total = self.service.list_records(self.db, mode=MODE_RAG)
        self.assertEqual(rag_total, 1)
        self.assertEqual(rag_rows[0].mode, ResearchMode.RAG)

    def test_detail_round_trip_preserves_snapshot_and_generation(self):
        generation = AnswerGeneration(
            mode="deterministic_fallback",
            degraded=True,
            failure_reason="authentication_failed",
            failure_message="模型服务鉴权失败",
            citation_check=CitationIntegrity(valid=False, invalid_ids=[7], available_count=1),
        )
        link = self.service.save_response(self.db, MODE_RAG, _rag_response(generation=generation))
        record = self.service.get_record(self.db, link.record_id)

        detail = build_research_detail(record)
        self.assertEqual(detail.status, "degraded")
        self.assertTrue(detail.generation.degraded)
        self.assertEqual(detail.generation.failure_reason, "authentication_failed")
        self.assertFalse(detail.generation.citation_check.valid)
        self.assertEqual(detail.generation.citation_check.invalid_ids, [7])
        self.assertEqual(detail.sources[0].block_id, 101)
        self.assertEqual(detail.sources[0].quote_text, "根据商馆会议记录，Browne 向 Howqua 支付货款。")
        self.assertEqual(detail.intent["time_filter"], 1792)

    def test_summary_row_shape(self):
        link = self.service.save_response(self.db, MODE_AGENT, _agent_response())
        summary = build_research_summary(self.service.get_record(self.db, link.record_id))
        self.assertEqual(summary.mode, "agent")
        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.source_count, 1)
        self.assertIsNone(summary.failure_reason)

    def test_missing_record_returns_none(self):
        self.assertIsNone(self.service.get_record(self.db, 4242))


class _FailingSession:
    """Session stub whose commit always fails, to prove failures are not hidden."""

    def __init__(self):
        self.rolled_back = False

    def add(self, obj):
        return None

    def commit(self):
        raise OperationalError("INSERT INTO research_sessions ...", {}, Exception("db down"))

    def rollback(self):
        self.rolled_back = True

    def refresh(self, obj):
        return None


class TestWriteFailureIsNotFaked(unittest.TestCase):
    def test_sqlalchemy_error_reports_unsaved(self):
        session = _FailingSession()
        link = ResearchHistoryService().save_response(session, MODE_RAG, _rag_response())
        self.assertFalse(link.saved)
        self.assertIsNone(link.record_id)
        self.assertIn("未能写入数据库", link.error)
        self.assertTrue(session.rolled_back, "a failed write must be rolled back")

    def test_failure_error_is_scrubbed(self):
        class LeakySession(_FailingSession):
            def commit(self):
                raise OperationalError(
                    "INSERT", {}, Exception("postgresql://ocr_platform:hunter2@127.0.0.1:5432/ocr_platform")
                )

        link = ResearchHistoryService().save_response(LeakySession(), MODE_RAG, _rag_response())
        self.assertFalse(link.saved)
        self.assertNotIn("hunter2", link.error)

    def test_failed_run_is_stored_as_failed_status(self):
        class RecordingSession(_FailingSession):
            def __init__(self):
                super().__init__()
                self.added = []

            def add(self, obj):
                self.added.append(obj)

            def commit(self):
                return None

            def refresh(self, obj):
                obj.id = 99

        session = RecordingSession()
        link = ResearchHistoryService().save_failure(session, MODE_AGENT, "Howqua 是谁", RuntimeError("boom"))
        self.assertTrue(link.saved)
        stored = session.added[0]
        self.assertEqual(stored.status, ResearchStatus.FAILED)
        self.assertEqual(stored.failure_reason, "research_pipeline_failed")
        self.assertEqual(stored.answer, "")
        self.assertEqual(stored.sources, [])


class TestHistoryApi(unittest.TestCase):
    """Router-level behaviour with a stub session and patched service."""

    def setUp(self):
        from app.api.routers import research as research_router_module

        self.module = research_router_module
        self.service = research_router_module.research_history_service

    def test_list_endpoint_shape(self):
        record = build_record(MODE_AGENT, _agent_response())
        record.id = 3
        from datetime import datetime

        record.created_at = datetime(2026, 10, 22, 10, 0, 0)

        with mock.patch.object(self.service, "list_records", return_value=([record], 1)):
            response = self.module.list_research_history(page=1, page_size=20, mode=None, db=None)

        self.assertEqual(response.total, 1)
        self.assertEqual(response.page, 1)
        self.assertEqual(response.page_size, 20)
        item = response.items[0]
        self.assertEqual(item.id, 3)
        self.assertEqual(item.mode, "agent")
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.source_count, 1)

    def test_list_endpoint_surfaces_database_error_as_503(self):
        from fastapi import HTTPException

        with mock.patch.object(
            self.service, "list_records", side_effect=OperationalError("SELECT", {}, Exception("no such table"))
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.module.list_research_history(page=1, page_size=20, mode=None, db=None)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("研究历史暂时不可用", ctx.exception.detail)

    def test_pagination_bounds_are_declared(self):
        """The Query(ge/le/pattern) constraints are what FastAPI turns into 422."""
        import inspect

        signature = inspect.signature(self.module.list_research_history)

        def bounds(name):
            meta = getattr(signature.parameters[name].default, "metadata", []) or []
            found = {}
            for item in meta:
                if hasattr(item, "ge"):
                    found["ge"] = item.ge
                if hasattr(item, "le"):
                    found["le"] = item.le
                if hasattr(item, "pattern"):
                    found["pattern"] = item.pattern
            return found

        self.assertEqual(bounds("page").get("ge"), 1)
        self.assertEqual(bounds("page_size").get("ge"), 1)
        self.assertEqual(bounds("page_size").get("le"), 100)
        self.assertEqual(bounds("mode").get("pattern"), "^(rag|agent)$")

    def test_detail_returns_stored_snapshot_without_rerunning_research(self):
        record = build_record(MODE_RAG, _rag_response())
        record.id = 5
        from datetime import datetime

        record.created_at = datetime(2026, 10, 22, 10, 0, 0)

        from app.services.rag import rag_service as rag_singleton
        from app.services.kg.llm_client import kg_llm_client

        with mock.patch.object(self.service, "get_record", return_value=record), mock.patch.object(
            rag_singleton, "answer_question", side_effect=AssertionError("history must not re-run RAG")
        ), mock.patch.object(
            kg_llm_client, "call_extraction_llm", side_effect=AssertionError("history must not call the model")
        ):
            detail = self.module.get_research_record(record_id=5, db=None)

        self.assertEqual(detail.id, 5)
        self.assertEqual(detail.answer, record.answer)
        self.assertEqual(detail.sources[0].block_id, 101)
        self.assertEqual(detail.sources[0].source_id, "证据 #1")
        self.assertEqual(detail.status, "completed")

    def test_detail_missing_record_is_404(self):
        from fastapi import HTTPException

        with mock.patch.object(self.service, "get_record", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                self.module.get_research_record(record_id=999, db=None)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", ctx.exception.detail)

    def test_detail_database_error_is_503(self):
        from fastapi import HTTPException

        with mock.patch.object(
            self.service, "get_record", side_effect=OperationalError("SELECT", {}, Exception("no such table"))
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.module.get_research_record(record_id=1, db=None)
        self.assertEqual(ctx.exception.status_code, 503)


class TestResearchRoutersPersistResults(unittest.IsolatedAsyncioTestCase):
    """The existing RAG/Agent endpoints must save what they actually returned."""

    def _ids(self, link):
        return mock.patch.object(
            history_module.research_history_service, "save_response", return_value=link
        )

    async def test_rag_endpoint_attaches_record_id(self):
        from app.api.routers import rag as rag_router_module
        from app.schemas.rag import RagQueryRequest

        response = _rag_response()
        with mock.patch.object(
            rag_router_module.rag_service, "answer_question", new=mock.AsyncMock(return_value=response)
        ), mock.patch.object(
            rag_router_module.research_history_service,
            "save_response",
            return_value=HistoryLink(saved=True, record_id=12, mode=MODE_RAG),
        ) as saver:
            result = await rag_router_module.query_graph_rag(
                request=RagQueryRequest(question="Howqua 与 Browne 的毛织品合同如何议定？"), db=None
            )

        self.assertEqual(result.history.record_id, 12)
        self.assertTrue(result.history.saved)
        kwargs = saver.call_args.kwargs
        self.assertEqual(kwargs["question"], "Howqua 与 Browne 的毛织品合同如何议定？")
        self.assertEqual(saver.call_args.args[1], MODE_RAG)

    async def test_agent_endpoint_attaches_record_id(self):
        from app.api.routers import agent as agent_router_module
        from app.schemas.agent import AgentResearchRequest

        response = _agent_response()
        with mock.patch.object(
            agent_router_module.agent_service, "run_research", new=mock.AsyncMock(return_value=response)
        ), mock.patch.object(
            agent_router_module.research_history_service,
            "save_response",
            return_value=HistoryLink(saved=True, record_id=13, mode=MODE_AGENT),
        ) as saver:
            result = await agent_router_module.run_agent_research(
                request=AgentResearchRequest(question="Howqua 与 Browne 的关系？"), db=None
            )

        self.assertEqual(result.history.record_id, 13)
        self.assertEqual(saver.call_args.args[1], MODE_AGENT)

    async def test_save_failure_keeps_answer_but_reports_unsaved(self):
        from app.api.routers import rag as rag_router_module
        from app.schemas.rag import RagQueryRequest

        response = _rag_response()
        with mock.patch.object(
            rag_router_module.rag_service, "answer_question", new=mock.AsyncMock(return_value=response)
        ), mock.patch.object(
            rag_router_module.research_history_service,
            "save_response",
            return_value=HistoryLink(saved=False, mode=MODE_RAG, error="研究记录未能写入数据库（db down）"),
        ):
            result = await rag_router_module.query_graph_rag(
                request=RagQueryRequest(question="Howqua 与 Browne 的毛织品合同如何议定？"), db=None
            )

        self.assertFalse(result.history.saved)
        self.assertIsNone(result.history.record_id)
        self.assertIn("未能写入数据库", result.history.error)
        # the answer itself is untouched and still not upgraded to success
        self.assertEqual(result.answer, response.answer)
        self.assertEqual(result.generation.mode, "model")

    async def test_agent_failure_is_recorded_and_reported(self):
        from fastapi import HTTPException
        from app.api.routers import agent as agent_router_module
        from app.schemas.agent import AgentResearchRequest

        with mock.patch.object(
            agent_router_module.agent_service,
            "run_research",
            new=mock.AsyncMock(side_effect=RuntimeError("retrieval exploded")),
        ), mock.patch.object(
            agent_router_module.research_history_service, "save_failure", return_value=HistoryLink(record_id=21)
        ) as recorder:
            with self.assertRaises(HTTPException) as ctx:
                await agent_router_module.run_agent_research(
                    request=AgentResearchRequest(question="Howqua 与 Browne 的关系？"), db=None
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(recorder.call_args.args[1], MODE_AGENT)
        self.assertEqual(recorder.call_args.args[2], "Howqua 与 Browne 的关系？")


class TestStartupDoesNotCreateResearchTable(unittest.TestCase):
    """``create_all()`` must never create the Alembic-managed research table."""

    def test_startup_table_list_excludes_alembic_managed_tables(self):
        from app import main as main_module

        names = {table.name for table in main_module._startup_tables()}
        self.assertNotIn("research_sessions", names)
        self.assertIn("documents", names)

    def test_model_is_registered_in_metadata_but_marked_alembic_managed(self):
        from app.models.research import ALEMBIC_MANAGED_TABLES

        self.assertIn("research_sessions", {t.name for t in Base.metadata.sorted_tables})
        self.assertIn("research_sessions", ALEMBIC_MANAGED_TABLES)


class TestRepositoryContract(unittest.TestCase):
    """The repository must not silently swallow a failing commit."""

    def test_add_propagates_commit_errors(self):
        session = _FailingSession()
        with self.assertRaises(OperationalError):
            ResearchRepository(session).add(ResearchSession(mode=ResearchMode.RAG, question="q", answer="a"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
