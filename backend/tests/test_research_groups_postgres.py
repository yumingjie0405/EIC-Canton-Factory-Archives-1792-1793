"""PostgreSQL integration tests for research groups, batch import and group scope.

Runs against the isolated, migrated test instance (0003_research_groups applied).
Only the language model is mocked; retrieval, scope enforcement, persistence and
the group endpoints run for real.
"""
import asyncio
import os
import unittest
from unittest import mock
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.api.routers import groups as groups_router
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
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.agent.tool_registry import tool_registry
from app.services.kg.llm_client import kg_llm_client
from app.services.research_group_service import GroupConflict, ResearchGroupService
from app.services.research_history_service import MODE_RAG
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-R26G]"
DOC_A_FILENAME = "Synthetic_R26G_Canton_1792_woollen.pdf"
DOC_B_FILENAME = "Synthetic_R26G_Canton_1793_tea.pdf"
GROUP_A = SYNTH + " Canton 1792-1793"
GROUP_B = SYNTH + " Canton 1793 only"

TEXT_A = (
    SYNTH + " At a Consultation in November 1792, Supercargo Henry Browne and the Hong "
    "merchant Howqua settled the woollen contract for the season. "
    "（合成测试记录：1792 年 11 月，大班 Browne 与行商 Howqua 议定毛织品合同。）"
)
TEXT_B = (
    SYNTH + " In 1793 the committee recorded the tea shipment and the departure of the "
    "supercargoes for Europe. （合成测试记录：1793 年茶叶装运与离港。）"
)
QUESTION = "1792年，谁与行商 Howqua 议定了毛织品合同？"
ANSWER = (
    "### 档案直接记载\n- 根据 [证据 #1]《" + DOC_A_FILENAME + "》原始记载：" + TEXT_A + "\n\n"
    "### 图谱关系事实\n- 未见独立关系记录。\n\n"
    "### 外部权威考据\n- 未见独立外部考据。\n\n"
    "### 历史分析与综合推断\n- 据此判断 1792 年毛织品合同由 Browne 与 Howqua 议定。\n\n"
    "### 档案未见记录与存疑说明\n- 未见其他争议记录。"
)


def _pdf_bytes(marker: str) -> bytes:
    return ("%PDF-1.4\n% " + marker + "\n%%EOF\n").encode("utf-8")


def _metrics():
    from app.schemas.kg_extraction_schema import ExtractionTaskMetrics

    return ExtractionTaskMetrics(
        model_name="group-mock", latency_ms=1.0,
        prompt_tokens=1, completion_tokens=1, total_tokens=2,
    )


@requires_postgres_test_db
class TestResearchGroupsPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls.doc_a = cls._make_document(DOC_A_FILENAME, TEXT_A, page_number=3)
        cls.doc_b = cls._make_document(DOC_B_FILENAME, TEXT_B, page_number=4)
        cls.person_a = Person(
            display_name=SYNTH + " Browne",
            canonical_name_en=SYNTH + " Browne",
            primary_role="Supercargo",
            identity_status=IdentityStatus.ACCEPTED,
        )
        cls.person_b = Person(
            display_name=SYNTH + " Howqua",
            canonical_name_zh=SYNTH + " 浩官",
            primary_role="Security Merchant",
            identity_status=IdentityStatus.ACCEPTED,
        )
        cls.db.add_all([cls.person_a, cls.person_b])
        cls.db.flush()
        cls.relationship = Relationship(
            source_person_id=cls.person_a.id,
            target_person_id=cls.person_b.id,
            relation_type=RelationType.COMMERCIAL,
            claim_status=ClaimStatus.ACCEPTED,
            review_status=ReviewStatusKG.REVIEWED,
            confidence=0.9,
            time_description="1792",
            evidence_summary=SYNTH + " woollen contract",
        )
        cls.db.add(cls.relationship)
        cls.db.flush()
        cls.db.add(
            RelationshipEvidence(
                relationship_id=cls.relationship.id,
                block_id=cls.doc_a["block_id"],
                quote_text=TEXT_A,
                evidence_role=EvidenceRole.SUPPORTS,
            )
        )
        cls.db.commit()

    @classmethod
    def _make_document(cls, filename, text, page_number=1):
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
        page = Page(document_id=document.id, page_number=page_number, width=1000, height=1400)
        cls.db.add(page)
        cls.db.flush()
        block = Block(
            page_id=page.id,
            block_index=0,
            block_label="paragraph",
            bbox=[80, 80, 900, 200],
            ocr_text_raw=text,
            ocr_text_corrected=text,
        )
        cls.db.add(block)
        cls.db.commit()
        return {"document_id": document.id, "page_id": page.id, "block_id": block.id}

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            db.query(ResearchSession).filter(
                ResearchSession.question.startswith(QUESTION)
            ).delete(synchronize_session=False)
            group_ids = [
                row[0]
                for row in db.query(ResearchGroup.id)
                .filter(ResearchGroup.name.in_([GROUP_A, GROUP_B]))
                .all()
            ]
            if group_ids:
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
                .filter(Document.filename.in_([DOC_A_FILENAME, DOC_B_FILENAME]))
                .all()
            ]
            person_ids = [
                row[0]
                for row in db.query(Person.id)
                .filter(Person.display_name.in_([SYNTH + " Browne", SYNTH + " Howqua"]))
                .all()
            ]
            if doc_ids:
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
            if person_ids:
                db.query(Relationship).filter(
                    Relationship.source_person_id.in_(person_ids)
                    | Relationship.target_person_id.in_(person_ids)
                ).delete(synchronize_session=False)
                db.query(Person).filter(Person.id.in_(person_ids)).delete(
                    synchronize_session=False
                )
            if doc_ids:
                page_ids = [
                    row[0]
                    for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
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
            # Imported files created by the batch tests
            leftovers = [
                row[0]
                for row in db.query(Document.file_path)
                .filter(Document.original_filename.like("synthetic_r26g_import_%"))
                .all()
            ]
            db.query(Document).filter(
                Document.original_filename.like("synthetic_r26g_import_%")
            ).delete(synchronize_session=False)
            db.commit()
            for path in leftovers:
                for candidate in (path, os.path.join("..", path)):
                    try:
                        if os.path.isfile(candidate):
                            os.remove(candidate)
                    except OSError:
                        pass
        except Exception:  # noqa: BLE001 - cleanup must not mask failures
            db.rollback()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        app.dependency_overrides.pop(get_db, None)
        cls.db.close()

    def setUp(self):
        # Every test starts with the two base documents and no groups.
        group_ids = [
            row[0]
            for row in self.db.query(ResearchGroup.id)
            .filter(ResearchGroup.name.in_([GROUP_A, GROUP_B]))
            .all()
        ]
        if group_ids:
            self.db.query(ResearchSession).filter(
                ResearchSession.group_id.in_(group_ids)
            ).delete(synchronize_session=False)
            self.db.query(ResearchGroupDocument).filter(
                ResearchGroupDocument.group_id.in_(group_ids)
            ).delete(synchronize_session=False)
            self.db.query(ResearchGroup).filter(ResearchGroup.id.in_(group_ids)).delete(
                synchronize_session=False
            )
            self.db.commit()

    # ------------------------------------------------------------- helpers
    def _mock_llm(self, text=ANSWER):
        return mock.patch.object(
            kg_llm_client, "call_extraction_llm", new=AsyncMock(return_value=(text, _metrics()))
        )

    def _make_group(self, name=GROUP_A, document_ids=None):
        group = self.service.create_group(GroupCreate(name=name, description="synthetic"))
        if document_ids:
            self.service.attach_documents(group.id, document_ids)
        return group

    def _fake_enqueue(self, calls):
        def enqueue(db, document):
            from app.models.task import Task, TaskStatus, TaskType

            # Mirrors the real pipeline: an OCR task is created before dispatch.
            task = Task(
                document_id=document.id,
                task_type=TaskType.OCR,
                status=TaskStatus.PENDING,
            )
            db.add(task)
            db.commit()
            calls.append(document.id)
            return "task-%s" % task.id

        return enqueue

    # --------------------------------------------------------------- tests
    def test_01_create_list_and_duplicate_name(self):
        created = self.client.post(
            "/api/groups", json={"name": GROUP_A, "description": "integration"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        group_id = created.json()["id"]

        duplicate = self.client.post("/api/groups", json={"name": GROUP_A})
        self.assertEqual(duplicate.status_code, 409)

        listing = self.client.get("/api/groups", params={"page_size": 100}).json()
        self.assertTrue(any(item["id"] == group_id for item in listing["items"]))

        with self.assertRaises(GroupConflict):
            self.service.create_group(GroupCreate(name=GROUP_A))

    def test_02_membership_is_the_only_thing_removed(self):
        group = self._make_group(document_ids=[self.doc_a["document_id"]])
        added, duplicates, missing, auto = self.service.attach_documents(
            group.id, [self.doc_a["document_id"], 999999]
        )
        self.assertEqual(added, [])
        self.assertEqual(duplicates, [self.doc_a["document_id"]])
        self.assertEqual(missing, [999999])
        # A duplicate membership is not a research event: nothing was queued.
        self.assertEqual(auto, [])

        detail = self.client.get("/api/groups/%d" % group.id).json()
        self.assertEqual(len(detail["documents"]), 1)
        self.assertEqual(detail["documents"][0]["document_id"], self.doc_a["document_id"])
        self.assertEqual(detail["documents"][0]["import_status"], "ready")

        removed = self.client.delete(
            "/api/groups/%d/documents/%d" % (group.id, self.doc_a["document_id"])
        )
        self.assertEqual(removed.status_code, 200)

        self.assertIsNone(
            self.db.query(ResearchGroupDocument)
            .filter(
                ResearchGroupDocument.group_id == group.id,
                ResearchGroupDocument.document_id == self.doc_a["document_id"],
            )
            .one_or_none()
        )
        # Archival data is untouched.
        self.assertIsNotNone(
            self.db.query(Document).filter(Document.id == self.doc_a["document_id"]).one_or_none()
        )
        self.assertIsNotNone(
            self.db.query(Block).filter(Block.id == self.doc_a["block_id"]).one_or_none()
        )

    def test_03_duplicate_membership_row_is_rejected_by_the_database(self):
        group = self._make_group(document_ids=[self.doc_a["document_id"]])
        with self.assertRaises(IntegrityError):
            self.db.add(
                ResearchGroupDocument(
                    group_id=group.id,
                    document_id=self.doc_a["document_id"],
                    import_status=GroupImportStatus.PENDING,
                )
            )
            self.db.commit()
        self.db.rollback()
        count = (
            self.db.query(ResearchGroupDocument)
            .filter(ResearchGroupDocument.group_id == group.id)
            .count()
        )
        self.assertEqual(count, 1)

    def test_04_batch_import_attaches_and_reports_status(self):
        group = self._make_group()
        calls = []
        result = self.service.import_documents(
            group.id,
            [
                ("synthetic_r26g_import_a.pdf", _pdf_bytes("A")),
                ("synthetic_r26g_import_b.pdf", _pdf_bytes("B")),
            ],
            enqueue=self._fake_enqueue(calls),
        )
        self.assertEqual(result.accepted_count, 2)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(len(calls), 2)

        status = self.client.get(
            "/api/groups/%d/imports/%s" % (group.id, result.batch_id)
        ).json()
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["status_counts"].get("queued"), 2)
        for item in status["items"]:
            self.assertTrue(item["document_id"])

    def test_05_import_dedupes_by_content_identity(self):
        group = self._make_group()
        payload = _pdf_bytes("dedupe")
        first = self.service.import_documents(
            group.id, [("synthetic_r26g_import_dup.pdf", payload)], enqueue=self._fake_enqueue([])
        )
        second = self.service.import_documents(
            group.id, [("synthetic_r26g_import_dup.pdf", payload)], enqueue=self._fake_enqueue([])
        )
        self.assertEqual(first.accepted_count, 1)
        self.assertTrue(first.items[0].created_document)
        self.assertEqual(second.accepted_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertTrue(second.items[0].duplicate)
        self.assertFalse(second.items[0].created_document)

        document_id = first.items[0].document_id
        memberships = (
            self.db.query(ResearchGroupDocument)
            .filter(ResearchGroupDocument.document_id == document_id)
            .count()
        )
        self.assertEqual(memberships, 1)
        # A document can still belong to a second group.
        other = self._make_group(name=GROUP_B)
        added, _, _, _ = self.service.attach_documents(other.id, [document_id])
        self.assertEqual(added, [document_id])

    def test_06_one_bad_file_does_not_break_the_batch(self):
        group = self._make_group()
        calls = []

        def flaky_enqueue(db, document):
            if document.original_filename.endswith("bad.pdf"):
                raise RuntimeError("queue unavailable")
            calls.append(document.id)
            return "task"

        result = self.service.import_documents(
            group.id,
            [
                ("synthetic_r26g_import_good.pdf", _pdf_bytes("good")),
                ("synthetic_r26g_import_bad.pdf", _pdf_bytes("bad")),
                ("synthetic_r26g_import_note.txt", b"plain text"),
            ],
            enqueue=flaky_enqueue,
        )
        self.assertEqual(result.accepted_count, 2)  # good + bad (attached, queue failed)
        self.assertEqual(result.failed_count, 1)    # the .txt is rejected
        statuses = {item.filename: item for item in result.items}
        self.assertEqual(statuses["synthetic_r26g_import_good.pdf"].import_status, GroupImportStatus.QUEUED)
        self.assertEqual(statuses["synthetic_r26g_import_bad.pdf"].import_status, GroupImportStatus.FAILED)
        self.assertIn("queue unavailable", statuses["synthetic_r26g_import_bad.pdf"].error or "")
        self.assertEqual(statuses["synthetic_r26g_import_note.txt"].import_status, GroupImportStatus.FAILED)

    def test_07_retry_reports_failure_reason_and_requeues(self):
        group = self._make_group()
        payload = _pdf_bytes("retry")

        def broken(db, document):
            raise RuntimeError("celery down")

        result = self.service.import_documents(
            group.id, [("synthetic_r26g_import_retry.pdf", payload)], enqueue=broken
        )
        document_id = result.items[0].document_id
        self.assertEqual(result.items[0].import_status, GroupImportStatus.FAILED)

        member = self.service.retry_document(group.id, document_id, enqueue=self._fake_enqueue([]))
        self.assertEqual(member["import_status"], GroupImportStatus.QUEUED)
        self.assertIsNone(member["import_error"])

    def test_08_rag_with_group_only_returns_group_documents(self):
        group = self._make_group(document_ids=[self.doc_a["document_id"]])
        with self._mock_llm():
            response = self.client.post(
                "/api/rag/query",
                json={"question": QUESTION, "time_filter": 1792, "group_id": group.id},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["evidence_sufficient"])
        self.assertEqual(body["group_id"], group.id)
        self.assertEqual(body["group_name"], GROUP_A)
        self.assertTrue(body["sources"])
        self.assertEqual(
            {source["document_id"] for source in body["sources"]},
            {self.doc_a["document_id"]},
        )
        for source in body["sources"]:
            self.assertEqual(source["block_id"], self.doc_a["block_id"])
            self.assertIsNotNone(source["page_number"])

    def test_09_rag_rejects_unknown_and_empty_groups(self):
        unknown = self.client.post(
            "/api/rag/query", json={"question": QUESTION, "group_id": 987654}
        )
        self.assertEqual(unknown.status_code, 404)

        empty_group = self._make_group(name=GROUP_B)
        with self._mock_llm():
            response = self.client.post(
                "/api/rag/query", json={"question": QUESTION, "group_id": empty_group.id}
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["evidence_sufficient"])
        self.assertEqual(body["sources"], [])
        self.assertIn("研究组", body["answer"])

    def test_10_graph_facts_outside_the_group_are_excluded(self):
        group = self._make_group(document_ids=[self.doc_a["document_id"]])
        with self._mock_llm():
            scoped = self.client.post(
                "/api/rag/query",
                json={"question": SYNTH + " Howqua Browne 关系", "group_id": group.id},
            ).json()
        self.assertEqual(
            {source["document_id"] for source in scoped["sources"]},
            {self.doc_a["document_id"]},
        )

        # A relationship evidenced only by document B must not appear for group A.
        self.db.add(
            RelationshipEvidence(
                relationship_id=self.relationship.id,
                block_id=self.doc_b["block_id"],
                quote_text=TEXT_B,
                evidence_role=EvidenceRole.SUPPORTS,
            )
        )
        self.db.commit()
        evidence = [
            item
            for item in scoped["sources"]
            if item["document_id"] == self.doc_b["document_id"]
        ]
        self.assertEqual(evidence, [])

    def test_11_agent_respects_the_group_scope(self):
        group = self._make_group(document_ids=[self.doc_a["document_id"]])
        with self._mock_llm():
            response = self.client.post(
                "/api/agent/research",
                json={"question": QUESTION, "time_filter": 1792, "group_id": group.id},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["group_id"], group.id)
        self.assertEqual(body["group_name"], GROUP_A)
        for source in body["sources"]:
            self.assertEqual(source["document_id"], self.doc_a["document_id"])

        unknown = self.client.post(
            "/api/agent/research", json={"question": QUESTION, "group_id": 987654}
        )
        self.assertEqual(unknown.status_code, 404)

    def test_12_agent_tool_contract_carries_the_scope(self):
        group = self._make_group(document_ids=[self.doc_a["document_id"]])
        record, output = asyncio.run(
            tool_registry.execute_tool(
                db=self.db,
                tool_name="search_archival_evidence",
                arguments={
                    "query": "woollen contract Howqua 1792",
                    "keywords": ["woollen", "contract"],
                    "time_filter": 1792,
                    "group_document_ids": [self.doc_a["document_id"]],
                },
                call_id="scope-test",
            )
        )
        self.assertEqual(record.status, "success")
        self.assertTrue(output)
        self.assertEqual({item["document_id"] for item in output}, {self.doc_a["document_id"]})

        record_empty, output_empty = asyncio.run(
            tool_registry.execute_tool(
                db=self.db,
                tool_name="search_archival_evidence",
                arguments={
                    "query": "woollen contract Howqua 1792",
                    "time_filter": 1792,
                    "group_document_ids": [],
                },
                call_id="scope-empty",
            )
        )
        self.assertEqual(record_empty.status, "success")
        self.assertEqual(output_empty, [])

    def test_13_history_records_keep_the_group_association(self):
        group_a = self._make_group(document_ids=[self.doc_a["document_id"]])
        group_b = self._make_group(name=GROUP_B, document_ids=[self.doc_b["document_id"]])
        with self._mock_llm():
            scoped = self.client.post(
                "/api/rag/query",
                json={"question": QUESTION + " group history", "group_id": group_a.id},
            ).json()
        record_id = scoped["history"]["record_id"]
        self.assertTrue(scoped["history"]["saved"])

        in_group = self.client.get(
            "/api/research/history", params={"group_id": group_a.id, "page_size": 50}
        ).json()
        row = next(item for item in in_group["items"] if item["id"] == record_id)
        self.assertEqual(row["group_name"], GROUP_A)

        other_group = self.client.get(
            "/api/research/history", params={"group_id": group_b.id, "page_size": 50}
        ).json()
        self.assertFalse(any(item["id"] == record_id for item in other_group["items"]))

        detail = self.client.get("/api/research/history/%d" % record_id).json()
        self.assertEqual(detail["group_id"], group_a.id)
        for source in detail["sources"]:
            self.assertEqual(source["document_id"], self.doc_a["document_id"])

    def test_15_http_import_endpoint_accepts_multipart_and_reports_status(self):
        group = self._make_group()

        def fake_enqueue(db, document):
            from app.models.task import Task, TaskStatus, TaskType

            db.add(Task(document_id=document.id, task_type=TaskType.OCR, status=TaskStatus.PENDING))
            db.commit()
            return "task"

        with mock.patch(
            "app.services.research_group_service._default_enqueue", fake_enqueue
        ):
            response = self.client.post(
                "/api/groups/%d/import" % group.id,
                files=[("files", ("synthetic_r26g_import_http.pdf", _pdf_bytes("http"), "application/pdf"))],
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["accepted_count"], 1)
        self.assertEqual(body["failed_count"], 0)
        document_id = body["items"][0]["document_id"]
        self.assertTrue(document_id)

        status = self.client.get(
            "/api/groups/%d/imports/%s" % (group.id, body["batch_id"])
        ).json()
        self.assertEqual(status["total"], 1)
        self.assertEqual(status["status_counts"].get("queued"), 1)

        # Unsupported extension is rejected before anything is stored.
        rejected = self.client.post(
            "/api/groups/%d/import" % group.id,
            files=[("files", ("notes.txt", b"plain", "text/plain"))],
        )
        self.assertEqual(rejected.status_code, 400)

    def test_16_global_research_is_unchanged(self):
        with self._mock_llm():
            response = self.client.post(
                "/api/rag/query", json={"question": QUESTION, "time_filter": 1792}
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body["group_id"])
        self.assertTrue(body["evidence_sufficient"])


    def test_17_import_failure_survives_status_refresh(self):
        group = self._make_group()

        def broken(db, document):
            raise RuntimeError("celery down")

        result = self.service.import_documents(
            group.id, [("synthetic_r26g_import_broker.pdf", _pdf_bytes("broker"))], enqueue=broken
        )
        item = result.items[0]
        self.assertEqual(item.import_status, GroupImportStatus.FAILED)
        self.assertIn("celery down", item.error or "")

        # import_documents ends with refresh_statuses(); the failure reason must
        # not be rewritten into a neutral "pending" state by that refresh.
        self.service.refresh_statuses(group.id)
        self.db.expire_all()
        member = (
            self.db.query(ResearchGroupDocument)
            .filter(
                ResearchGroupDocument.group_id == group.id,
                ResearchGroupDocument.document_id == item.document_id,
            )
            .one()
        )
        self.assertEqual(member.import_status, GroupImportStatus.FAILED)
        self.assertIn("celery down", member.import_error or "")

        payload = self.service.batch_status(group.id, result.batch_id)
        self.assertEqual(payload.status_counts.get("failed"), 1)
        detail = self.client.get("/api/groups/%d" % group.id).json()
        failed_member = [row for row in detail["documents"] if row["document_id"] == item.document_id][0]
        self.assertEqual(failed_member["import_status"], "failed")
        self.assertIn("celery down", failed_member["import_error"] or "")

    def test_18_broker_rejection_marks_the_task_failed(self):
        group = self._make_group()
        with mock.patch(
            "app.tasks.run_ocr_task.delay", side_effect=RuntimeError("broker down")
        ):
            result = self.service.import_documents(
                group.id, [("synthetic_r26g_import_real_broker.pdf", _pdf_bytes("real-broker"))]
            )
        item = result.items[0]
        self.assertEqual(item.import_status, GroupImportStatus.FAILED)
        self.assertIn("broker down", item.error or "")

        from app.models.task import Task, TaskStatus, TaskType

        latest = (
            self.db.query(Task)
            .filter(Task.document_id == item.document_id, Task.task_type == TaskType.OCR)
            .order_by(Task.id.desc())
            .first()
        )
        self.assertIsNotNone(latest)
        # A rejected broker call must not leave a "pending" task that would make
        # the membership look like a normal queued job.
        self.assertEqual(latest.status, TaskStatus.FAILED)

        payload = self.service.batch_status(group.id, result.batch_id)
        self.assertEqual(payload.status_counts.get("failed"), 1)
        self.assertEqual(len(payload.items), 1)
        self.assertEqual(payload.items[0].import_status, GroupImportStatus.FAILED)
        self.assertIn("broker down", payload.items[0].error or "")


    def test_19_retry_is_not_overwritten_by_the_previous_failure(self):
        group = self._make_group()

        def broken(db, document):
            raise RuntimeError("celery down")

        result = self.service.import_documents(
            group.id, [("synthetic_r26g_import_retry2.pdf", _pdf_bytes("retry2"))], enqueue=broken
        )
        document_id = result.items[0].document_id

        # The worker marked the document failed for the first attempt.
        document = self.db.query(Document).filter(Document.id == document_id).one()
        document.status = DocumentStatus.FAILED
        document.error_message = SYNTH + " synthetic worker failure"
        self.db.commit()

        member = self.service.retry_document(group.id, document_id, enqueue=self._fake_enqueue([]))
        self.assertEqual(member["import_status"], GroupImportStatus.QUEUED)

        # The detail endpoint refreshes statuses; the re-queued job must stay
        # visible instead of being pulled back to the previous failure.
        self.service.refresh_statuses(group.id)
        self.db.expire_all()
        membership = (
            self.db.query(ResearchGroupDocument)
            .filter(
                ResearchGroupDocument.group_id == group.id,
                ResearchGroupDocument.document_id == document_id,
            )
            .one()
        )
        self.assertEqual(membership.import_status, GroupImportStatus.QUEUED)
        self.assertIsNone(membership.import_error)


    def test_20_docx_import_is_searchable_without_ocr(self):
        from io import BytesIO

        from docx import Document as DocxDocument

        from app.models.task import Task

        def _docx(paragraphs):
            buffer = BytesIO()
            document = DocxDocument()
            document.add_heading(SYNTH + " Word import", level=1)
            for text in paragraphs:
                document.add_paragraph(text)
            document.save(buffer)
            return buffer.getvalue()

        payload = _docx([SYNTH + " 1792 woollen contract signed by Browne and Howqua."])
        group = self._make_group()
        response = self.client.post(
            "/api/groups/%d/import" % group.id,
            files=[
                (
                    "files",
                    (
                        "synthetic_r26g_import_word.docx",
                        payload,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["accepted_count"], 1)
        item = body["items"][0]
        self.assertEqual(item["import_status"], "ready")
        document_id = item["document_id"]

        detail = self.client.get("/api/groups/%d?refresh=false" % group.id).json()
        member = [row for row in detail["documents"] if row["document_id"] == document_id][0]
        self.assertEqual(member["import_status"], "ready")
        self.assertEqual(member["block_count"], 2)

        document = self.db.query(Document).filter(Document.id == document_id).one()
        self.assertEqual(document.file_type, DocumentType.DOCX)
        blocks = (
            self.db.query(Block)
            .join(Page, Page.id == Block.page_id)
            .filter(Page.document_id == document_id)
            .order_by(Block.block_index)
            .all()
        )
        self.assertEqual([block.block_label for block in blocks], ["heading", "paragraph"])
        self.assertIn("woollen contract", blocks[1].ocr_text_raw)
        # Word text is the document's own text, never an OCR "correction"
        self.assertIsNone(blocks[1].ocr_text_corrected)
        # No OCR job is created for a Word document.
        self.assertEqual(
            self.db.query(Task).filter(Task.document_id == document_id).count(), 0
        )

        repeat = self.client.post(
            "/api/groups/%d/import" % group.id,
            files=[("files", ("synthetic_r26g_import_word.docx", payload, "application/octet-stream"))],
        ).json()
        self.assertEqual(repeat["duplicate_count"], 1)
        self.assertEqual(repeat["accepted_count"], 0)

        upload = self.client.post(
            "/api/documents/upload",
            files=[
                (
                    "file",
                    (
                        "synthetic_r26g_import_word_upload.docx",
                        _docx([SYNTH + " Upload endpoint also reads Word text."]),
                        "application/octet-stream",
                    ),
                )
            ],
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(upload.json()["file_type"], "docx")
        self.assertEqual(upload.json()["page_count"], 1)

    def test_21_docx_without_text_is_rejected_with_a_reason(self):
        from io import BytesIO

        from docx import Document as DocxDocument

        buffer = BytesIO()
        empty = DocxDocument()
        empty.add_paragraph("   ")
        empty.save(buffer)

        group = self._make_group()
        response = self.client.post(
            "/api/groups/%d/import" % group.id,
            files=[("files", ("synthetic_r26g_import_word_empty.docx", buffer.getvalue(), "application/octet-stream"))],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["accepted_count"], 0)
        self.assertEqual(body["failed_count"], 1)
        self.assertIn("没有可导入的文本", body["items"][0]["error"] or "")


    def test_22_oversized_file_is_rejected_with_a_reason(self):
        from app.config import settings

        group = self._make_group()
        original = settings.max_upload_file_bytes
        try:
            settings.max_upload_file_bytes = 1024
            response = self.client.post(
                "/api/groups/%d/import" % group.id,
                files=[
                    (
                        "files",
                        (
                            "synthetic_r26g_import_oversized.docx",
                            b"x" * 4096,
                            "application/octet-stream",
                        ),
                    )
                ],
            )
            self.assertEqual(response.status_code, 413, response.text)
            detail = response.json()["detail"]
            self.assertIn("超过单文件上限", detail)
            self.assertIn("synthetic_r26g_import_oversized.docx", detail)

            upload = self.client.post(
                "/api/documents/upload",
                files=[("file", ("synthetic_r26g_import_oversized.docx", b"x" * 4096, "application/octet-stream"))],
            )
            self.assertEqual(upload.status_code, 413, upload.text)
            self.assertIn("超过单文件上限", upload.json()["detail"])
        finally:
            settings.max_upload_file_bytes = original

        # no membership was created for the rejected file
        detail = self.client.get("/api/groups/%d?refresh=false" % group.id).json()
        self.assertEqual(detail["documents"], [])


if __name__ == "__main__":
    unittest.main()
