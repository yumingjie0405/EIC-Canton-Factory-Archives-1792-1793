"""Stage 8 R2.5 — end-to-end research workflow against a real PostgreSQL database.

The chain exercised here is exactly the one the UI drives::

    POST /api/rag/query  ->  persisted research record
    GET  /api/research/history          -> the record is listed
    GET  /api/research/history/{id}     -> stored answer + evidence snapshot
    GET  /api/blocks/{id}/context       -> the cited block and its same-page neighbours

Only the language model is mocked (``kg_llm_client.call_extraction_llm``).
Retrieval, the citation gate, persistence and the archive-context lookups all run
for real against the isolated PostgreSQL test instance; the fixtures inserted here
are explicitly synthetic (``[SYNTH-R25]``) and are removed in ``tearDownClass``.
"""
import unittest
from unittest import mock
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import or_

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.kg import Person, Relationship, RelationshipEvidence
from app.models.kg.enums import (
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    RelationType,
    ReviewStatusKG,
)
from app.models.page import Page
from app.models.research import ResearchSession
from app.services.kg.llm_client import kg_llm_client
from app.services.rag import rag_service
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-R25]"
PRIMARY_FILENAME = "Synthetic_R25_Consultations_1792.pdf"
OTHER_FILENAME = "Synthetic_R25_Unrelated_1793.pdf"
#: A document forced onto a primary key the curated year table knows about.
COLLISION_FILENAME = "Synthetic_R25_Collision_1792.pdf"

PREV_TEXT = SYNTH + " The minutes of the preceding consultation were read. （合成测试记录：宣读上次会议纪要。）"
TARGET_TEXT = (
    SYNTH + " At a Consultation held on 21 November 1792, Supercargo Henry Browne "
    "and the Hong merchant Howqua settled the woollen contract for the season. "
    "（合成测试记录：1792 年 11 月 21 日，大班 Henry Browne 与行商 Howqua 议定当季毛织品合同。）"
)
NEXT_TEXT = SYNTH + " The committee returned thanks and the consultation closed. （合成测试记录：会议结束。）"

QUESTION = "Who settled the woollen contract with Howqua in 1792?"
AGENT_QUESTION = "1792年，谁与行商 Howqua 议定了毛织品合同？"
GIBBERISH_QUESTION = "zzzq vbnm plok 1855 quarterly ledger of nothing"
CITED_ANSWER = (
    SYNTH + " 根据 [证据 #1]，1792 年与行商 Howqua 议定毛织品合同的"
    "是 Supercargo Henry Browne。"
)
HALLUCINATED_ANSWER = SYNTH + " 依据 [证据 #99]，合同由某位未具名的商人议定。"
AGENT_ANSWER = (
    "### 档案直接记载\n"
    "- 根据 [证据 #1]《" + PRIMARY_FILENAME + "》记载：" + TARGET_TEXT + "\n\n"
    "### 图谱关系事实\n"
    "- Henry Browne 与 Howqua 存在 commercial 往来。\n\n"
    "### 外部学术考据\n"
    "- 未见独立外部考据。\n\n"
    "### 历史分析与综合推断\n"
    "- 据此可判断该季毛织品合同由 Browne 与 Howqua 议定。\n\n"
    "### 档案未见记录与存疑说明\n"
    "- 未见其他争议记录。"
)


class ModelUnauthorized(Exception):
    """Stands in for an SDK client error carrying HTTP 401."""

    status_code = 401


#: A credential-shaped, entirely fictional token used to prove scrubbing works.
#: Assembled at runtime so no key-shaped literal is stored in the repository.
FAKE_API_KEY = "-".join(("sk", "proj", "R25FAKE" * 4))


@requires_postgres_test_db
class TestResearchWorkflowEndToEnd(unittest.TestCase):
    """Question -> retrieval -> answer -> save -> history -> archive context."""

    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.saved_record_ids = []
        cls._create_fixtures()

    @classmethod
    def tearDownClass(cls):
        cls._remove_fixtures()
        app.dependency_overrides.pop(get_db, None)
        cls.db.close()

    # ------------------------------------------------------------------ fixtures
    @classmethod
    def _create_fixtures(cls):
        db = cls.db
        cls._remove_fixtures()

        cls.primary_doc = Document(
            filename=PRIMARY_FILENAME,
            original_filename=PRIMARY_FILENAME,
            file_path="storage/uploads/" + PRIMARY_FILENAME,
            file_type=DocumentType.PDF,
            file_size=2048,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        cls.other_doc = Document(
            filename=OTHER_FILENAME,
            original_filename=OTHER_FILENAME,
            file_path="storage/uploads/" + OTHER_FILENAME,
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        db.add_all([cls.primary_doc, cls.other_doc])
        db.flush()

        cls.page = Page(document_id=cls.primary_doc.id, page_number=7, width=1000, height=1400)
        db.add(cls.page)
        db.flush()

        cls.prev_block = Block(
            page_id=cls.page.id,
            block_index=0,
            block_label="paragraph",
            bbox=[80, 80, 900, 200],
            ocr_text_raw=PREV_TEXT,
            ocr_text_corrected=PREV_TEXT,
        )
        cls.target_block = Block(
            page_id=cls.page.id,
            block_index=1,
            block_label="paragraph",
            bbox=[80, 210, 900, 420],
            ocr_text_raw=TARGET_TEXT,
            ocr_text_corrected=TARGET_TEXT,
            modern_english="[MODEL-SUMMARY-R25] Browne and Howqua settled the woollen contract.",
            translation_zh="【模型译文-R25】布朗与浩官议定了毛织品合同。",
        )
        cls.next_block = Block(
            page_id=cls.page.id,
            block_index=2,
            block_label="paragraph",
            bbox=[80, 430, 900, 560],
            ocr_text_raw=NEXT_TEXT,
            ocr_text_corrected=NEXT_TEXT,
        )
        db.add_all([cls.prev_block, cls.target_block, cls.next_block])
        db.flush()

        cls.person_howqua = Person(
            display_name="Howqua (Wu Bingjian)",
            canonical_name_zh="伍秉鉴",
            canonical_name_en="Wu Bingjian",
            primary_role="Security Merchant",
            organization="E-wo Hong",
            identity_status=IdentityStatus.ACCEPTED,
        )
        cls.person_browne = Person(
            display_name="Henry Browne",
            canonical_name_zh="亨利·布朗",
            canonical_name_en="Henry Browne",
            primary_role="Supercargo",
            organization="East India Company Canton Factory",
            identity_status=IdentityStatus.ACCEPTED,
        )
        db.add_all([cls.person_howqua, cls.person_browne])
        db.flush()

        cls.relationship = Relationship(
            source_person_id=cls.person_browne.id,
            target_person_id=cls.person_howqua.id,
            relation_type=RelationType.COMMERCIAL,
            claim_status=ClaimStatus.ACCEPTED,
            review_status=ReviewStatusKG.REVIEWED,
            confidence=0.95,
            time_description="1792",
            evidence_summary=SYNTH + " Browne and Howqua settled the woollen contract.",
        )
        db.add(cls.relationship)
        db.flush()
        cls.relationship_evidence = RelationshipEvidence(
            relationship_id=cls.relationship.id,
            block_id=cls.target_block.id,
            quote_text=TARGET_TEXT,
            evidence_role=EvidenceRole.SUPPORTS,
        )
        db.add(cls.relationship_evidence)
        db.commit()

        cls.target_block_id = cls.target_block.id
        cls.page_id = cls.page.id
        cls.primary_doc_id = cls.primary_doc.id
        cls.other_doc_id = cls.other_doc.id

    @classmethod
    def _remove_fixtures(cls):
        """Remove only the rows this suite created (never other suites' data)."""
        db = cls.db
        try:
            doc_ids = [
                doc_id
                for (doc_id,) in db.query(Document.id)
                .filter(Document.filename.in_([PRIMARY_FILENAME, OTHER_FILENAME, COLLISION_FILENAME]))
                .all()
            ]
            if not doc_ids:
                db.rollback()
                return
            block_ids = [
                block_id
                for (block_id,) in db.query(Block.id)
                .join(Page, Page.id == Block.page_id)
                .filter(Page.document_id.in_(doc_ids))
                .all()
            ]
            if block_ids:
                db.query(RelationshipEvidence).filter(
                    RelationshipEvidence.block_id.in_(block_ids)
                ).delete(synchronize_session=False)
            person_ids = [
                person_id
                for (person_id,) in db.query(Person.id)
                .filter(Person.display_name.in_(["Howqua (Wu Bingjian)", "Henry Browne"]))
                .all()
            ]
            if person_ids:
                db.query(Relationship).filter(
                    Relationship.source_person_id.in_(person_ids)
                    | Relationship.target_person_id.in_(person_ids)
                ).delete(synchronize_session=False)
                db.query(Person).filter(Person.id.in_(person_ids)).delete(
                    synchronize_session=False
                )
            page_ids = [
                page_id
                for (page_id,) in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
            ]
            if page_ids:
                db.query(Block).filter(Block.page_id.in_(page_ids)).delete(
                    synchronize_session=False
                )
                db.query(Page).filter(Page.id.in_(page_ids)).delete(synchronize_session=False)
            db.query(Document).filter(Document.id.in_(doc_ids)).delete(synchronize_session=False)
            db.query(ResearchSession).filter(
                or_(
                    ResearchSession.question.in_([QUESTION, AGENT_QUESTION, GIBBERISH_QUESTION]),
                    ResearchSession.question.startswith(QUESTION + " "),
                )
            ).delete(synchronize_session=False)
            db.commit()
        except Exception:  # noqa: BLE001 - cleanup must never mask a test failure
            db.rollback()

    @classmethod
    def _delete_documents(cls, filenames):
        """Delete the given synthetic documents (and their pages/blocks)."""
        db = cls.db
        try:
            doc_ids = [
                row[0]
                for row in db.query(Document.id).filter(Document.filename.in_(filenames)).all()
            ]
            if not doc_ids:
                db.rollback()
                return
            block_ids = [
                row[0]
                for row in db.query(Block.id)
                .join(Page, Page.id == Block.page_id)
                .filter(Page.document_id.in_(doc_ids))
                .all()
            ]
            if block_ids:
                db.query(RelationshipEvidence).filter(
                    RelationshipEvidence.block_id.in_(block_ids)
                ).delete(synchronize_session=False)
            page_ids = [
                row[0] for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
            ]
            if page_ids:
                db.query(Block).filter(Block.page_id.in_(page_ids)).delete(
                    synchronize_session=False
                )
                db.query(Page).filter(Page.id.in_(page_ids)).delete(synchronize_session=False)
            db.query(Document).filter(Document.id.in_(doc_ids)).delete(synchronize_session=False)
            db.commit()
        except Exception:  # noqa: BLE001 - cleanup must never mask a test failure
            db.rollback()

    # ------------------------------------------------------------------- helpers
    def _mock_llm(self, text):
        from app.schemas.kg_extraction_schema import ExtractionTaskMetrics

        metrics = ExtractionTaskMetrics(
            model_name="synthetic-test-model",
            latency_ms=1.0,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )
        return mock.patch.object(
            kg_llm_client,
            "call_extraction_llm",
            new=AsyncMock(return_value=(text, metrics)),
        )

    def _post_rag(self, question=QUESTION, time_filter=1792):
        return self.client.post(
            "/api/rag/query",
            json={"question": question, "top_k": 5, "time_filter": time_filter},
        )

    def _query_once(self):
        """Run one successful, cited RAG query and return the parsed body."""
        with self._mock_llm(CITED_ANSWER):
            response = self._post_rag()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _remember(self, record_id):
        if record_id not in TestResearchWorkflowEndToEnd.saved_record_ids:
            TestResearchWorkflowEndToEnd.saved_record_ids.append(record_id)

    # --------------------------------------------------------------------- tests
    def test_01_query_returns_cited_answer_over_real_archive_text(self):
        body = self._query_once()

        self.assertTrue(body["evidence_sufficient"])
        self.assertGreaterEqual(len(body["sources"]), 1)
        cited = [s for s in body["sources"] if s["block_id"] == self.target_block_id]
        self.assertEqual(len(cited), 1, "retrieval must reach the synthetic archival block")
        self.assertIn(SYNTH, cited[0]["quote_text"])
        self.assertEqual(cited[0]["document_id"], self.primary_doc_id)
        self.assertEqual(cited[0]["page_number"], 7)

        self.assertIn("[证据 #1]", body["answer"])
        self.assertTrue(body["generation"]["citation_check"]["valid"])
        self.assertEqual(body["generation"]["citation_check"]["invalid_ids"], [])
        self.assertFalse(body["generation"]["degraded"])

    def test_02_query_result_is_persisted_and_linked(self):
        body = self._query_once()

        link = body["history"]
        self.assertTrue(link["saved"], link)
        self.assertIsInstance(link["record_id"], int)
        self.assertEqual(link["mode"], "rag")
        self._remember(link["record_id"])

        stored = (
            self.db.query(ResearchSession)
            .filter(ResearchSession.id == link["record_id"])
            .one_or_none()
        )
        self.assertIsNotNone(stored, "the record must exist in PostgreSQL")
        self.assertEqual(stored.question, QUESTION)
        self.assertEqual(stored.status.value, "completed")
        self.assertEqual(stored.answer, body["answer"])
        self.assertTrue(stored.evidence_sufficient)
        self.assertTrue(stored.citation_valid)
        snapshot = [s for s in stored.sources if s["block_id"] == self.target_block_id]
        self.assertEqual(len(snapshot), 1)
        self.assertIn(SYNTH, snapshot[0]["quote_text"])

    def test_03_history_list_exposes_the_saved_run(self):
        body = self._query_once()
        record_id = body["history"]["record_id"]
        self._remember(record_id)

        listing = self.client.get("/api/research/history", params={"mode": "rag", "page_size": 50})
        self.assertEqual(listing.status_code, 200, listing.text)
        payload = listing.json()
        self.assertGreaterEqual(payload["total"], 1)
        row = next((item for item in payload["items"] if item["id"] == record_id), None)
        self.assertIsNotNone(row, "the new record must appear in the history list")
        self.assertEqual(row["mode"], "rag")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["question"], QUESTION)
        self.assertGreaterEqual(row["source_count"], 1)
        self.assertTrue(row["citation_valid"])
        self.assertEqual(row["failure_reason"], None)

    def test_04_detail_restores_snapshot_and_never_reruns_research(self):
        body = self._query_once()
        record_id = body["history"]["record_id"]
        self._remember(record_id)

        with mock.patch.object(
            rag_service, "answer_question", side_effect=AssertionError("history must not re-run RAG")
        ), mock.patch.object(
            kg_llm_client,
            "call_extraction_llm",
            new=AsyncMock(side_effect=AssertionError("history must not call the model")),
        ):
            detail_response = self.client.get("/api/research/history/%d" % record_id)

        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        detail = detail_response.json()
        self.assertEqual(detail["id"], record_id)
        self.assertEqual(detail["question"], QUESTION)
        self.assertEqual(detail["answer"], body["answer"])
        self.assertEqual(detail["status"], "completed")
        self.assertTrue(detail["evidence_sufficient"])
        self.assertTrue(detail["generation"]["citation_check"]["valid"])

        by_block = {item["block_id"]: item for item in detail["sources"]}
        self.assertIn(self.target_block_id, by_block)
        stored = by_block[self.target_block_id]
        original = next(s for s in body["sources"] if s["block_id"] == self.target_block_id)
        self.assertEqual(stored["quote_text"], original["quote_text"])
        self.assertEqual(stored["document_id"], self.primary_doc_id)
        self.assertEqual(stored["page_number"], 7)

    def test_05_archive_context_returns_the_cited_block_and_its_neighbours(self):
        body = self._query_once()
        self._remember(body["history"]["record_id"])

        response = self.client.get(
            "/api/blocks/%d/context" % self.target_block_id,
            params={"document_id": self.primary_doc_id, "window": 1},
        )
        self.assertEqual(response.status_code, 200, response.text)
        context = response.json()

        self.assertEqual(context["block"]["block_id"], self.target_block_id)
        self.assertEqual(context["block"]["raw_text"], TARGET_TEXT)
        self.assertTrue(context["block"]["has_text"])
        self.assertEqual(context["document_id"], self.primary_doc_id)
        self.assertEqual(context["document_name"], PRIMARY_FILENAME)
        self.assertEqual(context["page_id"], self.page_id)
        self.assertEqual(context["page_number"], 7)
        self.assertEqual(context["neighbour_scope"], "same_page")

        previous = [b["block_id"] for b in context["previous"]]
        following = [b["block_id"] for b in context["next"]]
        self.assertEqual(previous, [self.prev_block.id])
        self.assertEqual(following, [self.next_block.id])
        self.assertIn(SYNTH, context["previous"][0]["raw_text"])
        self.assertIn(SYNTH, context["next"][0]["raw_text"])

        # Model-derived prose must never be presented as archival text.
        serialized = str(context)
        self.assertNotIn("MODEL-SUMMARY-R25", serialized)
        self.assertNotIn("模型译文-R25", serialized)

    def test_06_archive_context_never_leaks_across_documents(self):
        response = self.client.get(
            "/api/blocks/%d/context" % self.target_block_id,
            params={"document_id": self.other_doc_id, "window": 1},
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("SYNTH-R25", response.text)
        self.assertNotIn(TARGET_TEXT, response.text)

    def test_07_missing_block_context_is_404(self):
        response = self.client.get(
            "/api/blocks/987654321/context",
            params={"document_id": self.primary_doc_id, "window": 1},
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("SYNTH-R25", response.text)

    def test_08_model_authentication_failure_is_not_a_normal_answer(self):
        with mock.patch.object(
            kg_llm_client,
            "call_extraction_llm",
            new=AsyncMock(
                side_effect=ModelUnauthorized(
                    "Incorrect API key provided: %s (401)" % FAKE_API_KEY
                )
            ),
        ):
            response = self._post_rag(question=QUESTION + " authentication probe")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        generation = body["generation"]
        self.assertTrue(generation["degraded"])
        self.assertNotEqual(generation["mode"], "model")
        self.assertEqual(generation["failure_reason"], "authentication_failed")
        # The diagnostic may name the cause, but never echo the credential.
        self.assertNotIn(FAKE_API_KEY, response.text)
        self.assertNotIn(FAKE_API_KEY[3:20], response.text)

        self.assertTrue(body["history"]["saved"])
        record_id = body["history"]["record_id"]
        self._remember(record_id)
        detail_response = self.client.get("/api/research/history/%d" % record_id)
        detail = detail_response.json()
        self.assertEqual(detail["status"], "degraded")
        self.assertNotEqual(detail["status"], "completed")
        self.assertEqual(detail["generation"]["failure_reason"], "authentication_failed")
        self.assertNotIn(FAKE_API_KEY, detail_response.text)
        self.assertNotIn(FAKE_API_KEY[3:20], detail_response.text)

        stored = (
            self.db.query(ResearchSession).filter(ResearchSession.id == record_id).one_or_none()
        )
        self.assertIsNotNone(stored)
        self.assertNotIn(FAKE_API_KEY, stored.failure_detail or "")
        self.assertNotIn(FAKE_API_KEY[3:20], stored.failure_message or "")

    def test_09_out_of_range_citation_is_flagged_and_never_cleaned_up(self):
        with self._mock_llm(HALLUCINATED_ANSWER):
            response = self._post_rag(question=QUESTION + " citation probe")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        check = body["generation"]["citation_check"]
        self.assertFalse(check["valid"])
        self.assertEqual(check["invalid_ids"], [99])
        self.assertTrue(body["generation"]["degraded"])
        self.assertIn("[证据 #99]", body["answer"])

        detail = self.client.get("/api/research/history/%d" % body["history"]["record_id"]).json()
        self._remember(body["history"]["record_id"])
        self.assertFalse(detail["generation"]["citation_check"]["valid"])
        self.assertEqual(detail["generation"]["citation_check"]["invalid_ids"], [99])

    def test_10_no_evidence_question_is_refused_and_saved_as_such(self):
        with self._mock_llm(CITED_ANSWER):
            response = self.client.post(
                "/api/rag/query",
                json={"question": GIBBERISH_QUESTION, "top_k": 5},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        self.assertFalse(body["evidence_sufficient"])
        self.assertEqual(body["sources"], [])
        self.assertEqual(body["generation"]["mode"], "refused_no_evidence")
        self.assertNotIn("证据 #1", body["answer"])

        self.assertTrue(body["history"]["saved"])
        record_id = body["history"]["record_id"]
        self._remember(record_id)
        detail = self.client.get("/api/research/history/%d" % record_id).json()
        self.assertEqual(detail["status"], "insufficient_evidence")
        self.assertFalse(detail["evidence_sufficient"])

    def test_11_agent_run_is_saved_and_reopenable(self):
        with self._mock_llm(AGENT_ANSWER):
            response = self.client.post(
                "/api/agent/research",
                json={"question": AGENT_QUESTION, "time_filter": 1792},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["history"]["saved"], body["history"])
        record_id = body["history"]["record_id"]
        self._remember(record_id)

        listing = self.client.get("/api/research/history", params={"mode": "agent", "page_size": 50})
        rows = [item for item in listing.json()["items"] if item["id"] == record_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "agent")

        detail = self.client.get("/api/research/history/%d" % record_id).json()
        self.assertEqual(detail["mode"], "agent")
        self.assertEqual(detail["answer"], body["final_answer"])
        self.assertEqual(detail["status"], rows[0]["status"])
        self.assertEqual([s["block_id"] for s in detail["sources"]],
                         [s["block_id"] for s in body["sources"]])

        # The stored run must be internally consistent: a cited answer needs
        # sources, and a refusal must never carry a fabricated citation.
        if body["evidence_sufficient"]:
            self.assertGreaterEqual(len(body["sources"]), 1)
            self.assertIn("证据 #1", body["final_answer"])
            self.assertNotIn("insufficient_evidence", detail["status"])
            self.assertGreaterEqual(rows[0]["source_count"], 1)
        else:
            self.assertEqual(body["sources"], [])
            self.assertEqual(detail["status"], "insufficient_evidence")
            self.assertNotIn("证据 #", body["final_answer"])

    def test_12_existing_records_survive_a_second_run(self):
        first = self._query_once()
        first_id = first["history"]["record_id"]
        self._remember(first_id)
        before = self.client.get("/api/research/history/%d" % first_id).json()

        second = self._query_once()
        self._remember(second["history"]["record_id"])
        self.assertNotEqual(first_id, second["history"]["record_id"])

        after = self.client.get("/api/research/history/%d" % first_id).json()
        self.assertEqual(after["answer"], before["answer"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(len(after["sources"]), len(before["sources"]))

    def test_13_curated_document_id_collision_cannot_hide_a_matching_document(self):
        """Regression for the R2.5 intermittent empty retrieval.

        ``CURATED_DOCUMENT_YEARS`` is keyed by primary key. Here a 1792 document is
        forced onto a curated id whose range does *not* cover 1792: the strict
        single-year filter used to drop it, so a questionable-by-id document
        produced "no evidence" at random. The filename must win.
        """
        from app.services.rag import temporal_resolver as temporal_module
        from app.services.rag.vector_retriever import vector_retriever

        curated = temporal_module.temporal_resolver.CURATED_DOCUMENT_YEARS
        taken = {row[0] for row in self.db.query(Document.id).all()}
        collision_id = next(
            doc_id
            for doc_id in sorted(curated)
            if doc_id not in taken and not (curated[doc_id][0] <= 1792 <= curated[doc_id][1])
        )
        curated_range = curated[collision_id][:2]

        text = (
            SYNTH + " Collision ledger entry: the woollen contract of 1792 was settled "
            "between Supercargo Henry Browne and the Hong merchant Howqua. "
            "（合成测试记录：1792 年毛织品合同由 Browne 与 Howqua 议定。）"
        )
        document = Document(
            id=collision_id,
            filename=COLLISION_FILENAME,
            original_filename=COLLISION_FILENAME,
            file_path="storage/uploads/" + COLLISION_FILENAME,
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        self.db.add(document)
        self.db.flush()
        page = Page(document_id=collision_id, page_number=4, width=1000, height=1400)
        self.db.add(page)
        self.db.flush()
        block = Block(
            page_id=page.id,
            block_index=0,
            block_label="paragraph",
            bbox=[80, 80, 900, 200],
            ocr_text_raw=text,
            ocr_text_corrected=text,
        )
        self.db.add(block)
        self.db.commit()
        # Keep the rest of the suite deterministic: only the primary document is
        # left carrying matching 1792 text once this test finishes.
        self.addCleanup(self._delete_documents, [COLLISION_FILENAME])

        info = temporal_module.temporal_resolver.resolve_document_temporal_info(
            collision_id, COLLISION_FILENAME
        )
        self.assertEqual(
            (info["start_year"], info["end_year"]),
            (1792, 1792),
            "filename must override a contradicting curated id (curated was %s)" % (curated_range,),
        )
        self.assertEqual(info["provenance"], "filename_inferred")
        self.assertIn(collision_id, temporal_module.temporal_resolver.get_document_ids_for_year(self.db, 1792))

        evidence = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="woollen contract Howqua collision ledger",
            keywords=["woollen", "contract", "collision"],
            entities=["Howqua (Wu Bingjian)"],
            top_k=5,
            time_filter=1792,
        )
        self.assertTrue(
            any(item.block_id == block.id for item in evidence),
            "the 1792 document must be retrievable regardless of its primary key",
        )
        self.assertEqual(
            [item.document_id for item in evidence if item.block_id == block.id],
            [collision_id],
        )

    def test_14_repeated_agent_runs_are_stable_on_fixed_data(self):
        """The same question, data and parameters must always produce the same verdict."""
        runs = 6
        verdicts = []
        with self._mock_llm(AGENT_ANSWER):
            for _ in range(runs):
                response = self.client.post(
                    "/api/agent/research",
                    json={"question": AGENT_QUESTION, "time_filter": 1792},
                )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertTrue(body["history"]["saved"], body["history"])
                self._remember(body["history"]["record_id"])
                verdicts.append(
                    (
                        body["evidence_sufficient"],
                        tuple(sorted(s["block_id"] for s in body["sources"])),
                        body["final_answer"].startswith("### 档案未见记录与存疑说明"),
                    )
                )

        self.assertEqual(
            len(set(verdicts)),
            1,
            "agent runs diverged on identical input: %s" % (verdicts,),
        )
        self.assertTrue(verdicts[0][0], "the synthetic corpus can answer this question")
        self.assertEqual(verdicts[0][1], (self.target_block_id,))

    def test_15_tool_argument_failure_is_visible_and_never_becomes_evidence(self):
        from app.services.agent.agent_runner import agent_runner

        def broken_selection(self, **_kwargs):
            # ``query`` is required by the tool contract; omitting it must surface
            # as a tool failure, not as a silently empty (but "successful") search.
            return [("search_archival_evidence", {"keywords": ["woollen"], "time_filter": 1792})]

        with self._mock_llm(AGENT_ANSWER), mock.patch.object(
            type(agent_runner), "_select_tools_for_step", broken_selection
        ):
            response = self.client.post(
                "/api/agent/research",
                json={"question": AGENT_QUESTION, "time_filter": 1792},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(any("工具 search_archival_evidence 执行失败" in w for w in body["warnings"]), body["warnings"])
        self.assertEqual(body["sources"], [])
        self.assertFalse(body["evidence_sufficient"])
        self.assertTrue(body["history"]["saved"])
        self._remember(body["history"]["record_id"])

        detail = self.client.get("/api/research/history/%d" % body["history"]["record_id"]).json()
        self.assertEqual(detail["status"], "insufficient_evidence")
        self.assertFalse(detail["evidence_sufficient"])


if __name__ == "__main__":
    unittest.main()
