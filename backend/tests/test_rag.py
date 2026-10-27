"""Automated unit and integration test suite for Stage 6: Graph RAG."""
import unittest
from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.test_support.db_fixtures import (
    drop_test_schema,
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)
from app.main import app
from app.models.block import Block
from app.models.document import Document
from app.models.page import Page
from app.models.kg import ExternalAuthority, Person, PersonMention, Relationship, RelationshipEvidence
from app.models.kg.enums import (
    AuthoritySource,
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.services.rag import (
    context_builder,
    graph_retriever,
    query_analyzer,
    rag_service,
    vector_retriever,
)
from unittest.mock import AsyncMock, patch
from app.schemas.rag import RagQueryRequest


@requires_postgres_test_db
class TestGraphRAG(unittest.TestCase):
    """Test suite for Graph RAG Query Understanding, Retrieval, and Anti-Hallucination."""

    @classmethod
    def setUpClass(cls):
        # Test isolation: never the application engine. The guard in
        # app.database_guard refuses any non-test target first.
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db

        cls.client = TestClient(app)

        cls.llm_patcher = patch.object(
            rag_service,
            "_generate_llm_answer",
            new_callable=AsyncMock,
            return_value="根据 [证据 #1]，在1792年，东印度公司大班 Henry Browne 与十三行行商 Howqua (伍秉鉴) 就当季毛织品合同进行了商议并达成协议。",
        )
        cls.mock_llm = cls.llm_patcher.start()

        # 确保基础文档与文本块存在
        doc = cls.db.query(Document).first()
        if not doc:
            from app.models.document import DocumentType, DocumentStatus
            doc = Document(
                filename="Canton_Consultations_1792.pdf",
                original_filename="Canton_Consultations_1792.pdf",
                file_path="storage/uploads/Canton_Consultations_1792.pdf",
                file_type=DocumentType.PDF,
                file_size=1024,
                page_count=1,
                status=DocumentStatus.COMPLETED,
            )
            cls.db.add(doc)
            cls.db.flush()

        page = cls.db.query(Page).filter_by(document_id=doc.id).first()
        if not page:
            page = Page(document_id=doc.id, page_number=2, width=1000, height=1400)
            cls.db.add(page)
            cls.db.flush()

        block = cls.db.query(Block).first()
        if not block:
            block = Block(
                page_id=page.id,
                block_index=0,
                block_label="paragraph",
                bbox=[100, 100, 500, 500],
                ocr_text_raw="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled the woolen contracts.",
                ocr_text_corrected="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled the woolen contracts for the current season.",
            )
            cls.db.add(block)
            cls.db.flush()
        cls.test_block_id = block.id

        # 确保人物 Howqua 与 Henry Browne 存在
        p_howqua = cls.db.query(Person).filter(Person.display_name.ilike("%Howqua%")).first()
        if not p_howqua:
            p_howqua = Person(
                display_name="Howqua (Wu Bingjian)",
                canonical_name_zh="伍秉鉴",
                canonical_name_en="Wu Bingjian",
                primary_role="Security Merchant",
                organization="E-wo Hong",
                birth_year=1769,
                death_year=1843,
                identity_status=IdentityStatus.ACCEPTED,
            )
            cls.db.add(p_howqua)
            cls.db.flush()
        cls.howqua_id = p_howqua.id

        p_browne = cls.db.query(Person).filter(Person.display_name.ilike("%Browne%")).first()
        if not p_browne:
            p_browne = Person(
                display_name="Henry Browne",
                canonical_name_zh="亨利·布朗",
                canonical_name_en="Henry Browne",
                primary_role="Supercargo",
                organization="East India Company Canton Factory",
                birth_year=1754,
                death_year=1812,
                identity_status=IdentityStatus.ACCEPTED,
            )
            cls.db.add(p_browne)
            cls.db.flush()
        cls.browne_id = p_browne.id

        # 确保两者之间存在关系与证据
        rel = cls.db.query(Relationship).filter(
            Relationship.source_person_id == p_browne.id,
            Relationship.target_person_id == p_howqua.id,
        ).first()
        if not rel:
            rel = Relationship(
                source_person_id=p_browne.id,
                target_person_id=p_howqua.id,
                relation_type=RelationType.COMMERCIAL,
                claim_status=ClaimStatus.ACCEPTED,
                review_status=ReviewStatusKG.REVIEWED,
                confidence=0.95,
                time_description="1792",
                evidence_summary="Henry Browne and Howqua settled woolen contracts for 1792.",
            )
            cls.db.add(rel)
            cls.db.flush()

            ev = RelationshipEvidence(
                relationship_id=rel.id,
                block_id=cls.test_block_id,
                quote_text="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled the woolen contracts for the current season.",
                evidence_role=EvidenceRole.SUPPORTS,
            )
            cls.db.add(ev)

        # 挂接一条外部考证记录
        auth = cls.db.query(ExternalAuthority).filter_by(person_id=p_howqua.id).first()
        if not auth:
            auth = ExternalAuthority(
                person_id=p_howqua.id,
                authority_source=AuthoritySource.LIANG_CANTON,
                record_title="梁嘉彬《广东十三行考》怡和行总商考",
                historical_notes="伍国莹为初代浩官，其子伍秉鉴继业成为著名总商。",
            )
            cls.db.add(auth)

        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_01_query_analyzer_entities_and_intent(self):
        """Test Query understanding, intent classification, and entity extraction."""
        # 事实问题
        intent1 = query_analyzer.analyze_query("浩官是谁？", db=self.db)
        self.assertEqual(intent1.query_type, "factual")
        self.assertTrue(any("Howqua" in e for e in intent1.entities))

        # 关系问题
        intent2 = query_analyzer.analyze_query("Henry Browne和Howqua是什么关系？", db=self.db)
        self.assertEqual(intent2.query_type, "relational")
        self.assertTrue(len(intent2.entities) >= 2)

        # 历史分析与年份解析
        intent3 = query_analyzer.analyze_query("东印度公司在1792年为什么调整羊毛贸易？", db=self.db)
        self.assertEqual(intent3.query_type, "historical_analysis")
        self.assertEqual(intent3.time_filter, 1792)
        self.assertIn("东印度公司", intent3.entities)

    def test_02_graph_retriever(self):
        """Test structured subgraph retrieval from KG."""
        entities, rels, evidences = graph_retriever.retrieve_graph_context(
            db=self.db,
            entity_names=["Howqua (Wu Bingjian)", "Henry Browne"],
        )
        self.assertTrue(len(entities) >= 1)
        self.assertTrue(len(rels) >= 1)
        # Check external authority presence
        howqua_ref = next((e for e in entities if "Howqua" in e.display_name), None)
        self.assertIsNotNone(howqua_ref)
        self.assertTrue(howqua_ref.external_authorities_count >= 1)

    def test_03_vector_retriever(self):
        """Test vector & text retrieval with grounded documents and pages."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="Henry Browne Howqua woolen contracts",
            keywords=["contracts", "woolen"],
            entities=["Howqua", "Henry Browne"],
            top_k=3,
        )
        self.assertTrue(len(evidences) >= 1)
        top_ev = evidences[0]
        self.assertIn("Brown", top_ev.quote_text)
        self.assertIsNotNone(top_ev.block_id)
        self.assertTrue(top_ev.relevance_score > 0.0)

    def test_04_context_builder_and_anti_hallucination(self):
        """Test ContextBuilder fusion and evidence sufficiency gate."""
        intent = query_analyzer.analyze_query("无中生有的虚构历史人物测试？", db=self.db)
        context, sources, is_sufficient = context_builder.fuse_and_build_context(
            query="无中生有的虚构历史人物测试？",
            intent=intent,
            entities=[],
            relationships=[],
            graph_evidences=[],
            text_evidences=[],
        )
        self.assertFalse(is_sufficient, "Empty evidence must trigger sufficiency gate failure")
        self.assertEqual(len(sources), 0)

    def test_05_api_factual_query(self):
        """Test 1: Factual query endpoint returns entity profile and research answer."""
        response = self.client.post(
            "/api/rag/query",
            json={"question": "浩官是谁？其在广州十三行担任什么角色？"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["evidence_sufficient"])
        self.assertTrue(len(data["entities"]) >= 1)
        self.assertIn("Howqua", data["entities"][0]["display_name"])
        self.assertTrue(len(data["answer"]) > 10)

    def test_06_api_relational_query(self):
        """Test 2: Relational query endpoint cites relationships and archival quote."""
        response = self.client.post(
            "/api/rag/query",
            json={"question": "Henry Browne和Howqua是什么关系？"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["evidence_sufficient"])
        self.assertTrue(len(data["relationships"]) >= 1)
        rel = data["relationships"][0]
        self.assertEqual(rel["relation_type"], "commercial")
        self.assertTrue(len(data["sources"]) >= 1)

    def test_07_api_historical_analysis_query(self):
        """Test 3: Historical analysis query fuses graph facts and archival text blocks."""
        response = self.client.post(
            "/api/rag/query",
            json={"question": "1792年东印度公司与行商之间的毛织品贸易是如何开展的？"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["evidence_sufficient"])
        has_woolen_cloth = any(
            "woolen" in s["quote_text"].lower() or "cloth" in s["quote_text"].lower()
            for s in data["sources"]
        )
        self.assertTrue(has_woolen_cloth, "Retrieved sources should contain woolen or cloth trade evidence")

    def test_08_api_nonexistent_question_refusal(self):
        """Test 4: Query about nonexistent/unrecorded historical event is refused without hallucination."""
        response = self.client.post(
            "/api/rag/query",
            json={"question": "拿破仑在广州十三行商馆进行了什么军事演习？"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["evidence_sufficient"], "Must recognize insufficient evidence")
        self.assertIn("当前知识库没有足够证据", data["answer"])
        self.assertEqual(len(data["sources"]), 0)

    def test_09_api_entity_rag_context(self):
        """Test entity context endpoint /api/rag/context/{entity_id}."""
        response = self.client.get(f"/api/rag/context/{self.howqua_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["entity"]["id"], self.howqua_id)
        self.assertTrue(len(data["relationships"]) >= 1)

    def test_10_api_analyze_query(self):
        """Test standalone query intent analyze endpoint."""
        response = self.client.post(
            "/api/rag/analyze?query_text=1793年马戛尔尼使团抵粤情况",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["time_filter"], 1793)

    @classmethod
    def tearDownClass(cls):
        cls.llm_patcher.stop()
        cls.db.close()
        app.dependency_overrides.pop(get_db, None)
        drop_test_schema(cls.engine)


if __name__ == "__main__":
    unittest.main()
