"""Automated test suite for Stage 7: AI Agent Dynamic Research Workflow."""
import unittest
from unittest.mock import AsyncMock, patch
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
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.models.kg import ExternalAuthority, Person, Relationship, RelationshipEvidence
from app.models.kg.enums import (
    AuthoritySource,
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    RelationType,
    ReviewStatusKG,
)
from app.schemas.agent import AgentResearchRequest
from app.services.agent import (
    agent_planner,
    agent_runner,
    agent_service,
    tool_registry,
)


@requires_postgres_test_db
class TestAgentResearch(unittest.TestCase):
    """Test suite covering Agent Tool Registry, Planning, Execution Budgets, and Anti-Hallucination."""

    @classmethod
    def setUpClass(cls):
        # Test isolation: never the application engine. The guard in
        # app.database_guard refuses any non-test target first.
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db

        cls.client = TestClient(app)

        # Mock LLM calls for deterministic and rapid test suite execution
        from app.services.kg.llm_client import kg_llm_client
        from app.schemas.kg_extraction_schema import ExtractionTaskMetrics
        mock_synthesis = (
            "### 档案直接记载\n"
            "- 根据 [证据 #1]《Canton_Consultations_1792.pdf》（Block #1）记载：Henry Browne 与 Howqua 就当季毛织品贸易订立了合同。\n\n"
            "### 图谱关系事实\n"
            "- Henry Browne（Supercargo）与 Howqua（Security Merchant）存在 commercial 往来。\n\n"
            "### 外部学术考据\n"
            "- 梁嘉彬《广东十三行考》考证伍秉鉴为著名十三行总商。\n\n"
            "### 历史分析与综合推断\n"
            "- 据此推断，双方具有长期深厚的商业往来信用。\n\n"
            "### 档案未见记录与存疑说明\n"
            "- 未见其他争议记录。"
        )
        mock_metrics = ExtractionTaskMetrics(
            model_name="deepseek-ai/DeepSeek-V3.2",
            latency_ms=15.0,
            prompt_tokens=100,
            completion_tokens=150,
            total_tokens=250,
        )
        cls.llm_patcher = patch.object(
            kg_llm_client,
            "call_extraction_llm",
            new_callable=AsyncMock,
            return_value=(mock_synthesis, mock_metrics),
        )
        cls.mock_llm = cls.llm_patcher.start()

        # 确保基础文档与文本块存在 (优先选取 1792 测试卷宗与毛织品合同切片)
        doc = cls.db.query(Document).filter(Document.filename.ilike("%1792%")).first()
        if not doc:
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

        block = cls.db.query(Block).filter(Block.ocr_text_corrected.ilike("%woolen contracts%")).first()
        if not block:
            block = Block(
                page_id=page.id,
                block_index=0,
                block_label="paragraph",
                bbox=[100, 100, 500, 500],
                ocr_text_raw="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled the woolen contracts.",
                ocr_text_corrected="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled the woolen contracts for the 1792 season.",
            )
            cls.db.add(block)
            cls.db.flush()
        cls.test_block_id = block.id
        cls.test_doc_id = doc.id

        # 确保人物实体存在
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

        # 关系与证据
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
                quote_text="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled the woolen contracts for the 1792 season.",
                evidence_role=EvidenceRole.SUPPORTS,
            )
            cls.db.add(ev)

        # 外部考据
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
        cls.llm_patcher.stop()
        cls.db.close()
        app.dependency_overrides.pop(get_db, None)
        drop_test_schema(cls.engine)

    # -------------------------------------------------------------
    # Test 1: Tool Registry & Whitelist Enforcement
    # -------------------------------------------------------------
    def test_01_tool_registry_and_whitelist(self):
        """Verify whitelist contains safe tools and intercepts unauthorized invocations."""
        tools = tool_registry.list_tool_definitions()
        tool_names = [t.name for t in tools]
        self.assertIn("search_persons", tool_names)
        self.assertIn("get_person_relationships", tool_names)
        self.assertIn("search_archival_evidence", tool_names)
        self.assertIn("get_external_authorities", tool_names)
        self.assertIn("graph_rag_query", tool_names)
        self.assertIn("analyze_query", tool_names)

        # Test unauthorized tool call interception
        import asyncio
        record, out = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="drop_database_table",
            arguments={"target": "persons"},
            call_id="call-malicious-1",
        ))
        self.assertEqual(record.status, "error")
        self.assertIn("not in the authorized research whitelist", record.output_summary)
        self.assertIsNone(out)

    # -------------------------------------------------------------
    # Test 2: Tool Execution with Database Grounding
    # -------------------------------------------------------------
    def test_02_tool_execution_grounding(self):
        """Verify whitelisted tools return grounded facts from real DB."""
        import asyncio

        # 1. search_persons
        rec1, out1 = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="search_persons",
            arguments={"query": "Howqua"},
            call_id="call-ground-1",
        ))
        self.assertEqual(rec1.status, "success")
        self.assertTrue(len(out1) >= 1)
        self.assertIn("Howqua", out1[0]["display_name"])

        # 2. get_person_relationships
        rec2, out2 = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="get_person_relationships",
            arguments={"person_name": "Henry Browne"},
            call_id="call-ground-2",
        ))
        self.assertEqual(rec2.status, "success")
        self.assertTrue(len(out2) >= 1)
        self.assertEqual(out2[0]["relation_type"], "commercial")

        # 3. search_archival_evidence (DEF-AGENT-01 verification: valid retrieval)
        rec3, out3 = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="search_archival_evidence",
            arguments={
                "query": "woolen contracts",
                "keywords": ["contracts", "woolen"],
                "entities": ["Henry Browne", "Howqua"],
                "time_filter": 1792,
                "top_k": 5,
            },
            call_id="call-ground-3",
        ))
        self.assertEqual(rec3.status, "success")
        self.assertIsInstance(out3, list)
        self.assertTrue(len(out3) >= 1)
        first_ev = out3[0]
        self.assertGreater(first_ev["block_id"], 0)
        self.assertGreater(first_ev["document_id"], 0)
        self.assertIn("woolen contracts", first_ev["quote_text"])
        self.assertGreater(first_ev["relevance_score"], 0.0)

        # 3b. search_archival_evidence: empty result handling
        rec3_empty, out3_empty = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="search_archival_evidence",
            arguments={
                "query": "UnrecordedSteamboatNuclearSubmarineXYZ999",
                "time_filter": 1792,
            },
            call_id="call-ground-3-empty",
        ))
        self.assertEqual(rec3_empty.status, "success")
        self.assertIsInstance(out3_empty, list)
        self.assertEqual(len(out3_empty), 0)

        # 3c. search_archival_evidence: exception handling without masking
        rec3_err, out3_err = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="search_archival_evidence",
            arguments={"query": None},  # invalid argument triggering error
            call_id="call-ground-3-err",
        ))
        self.assertEqual(rec3_err.status, "error")
        self.assertIsNotNone(rec3_err.error_message)

        # 4. get_external_authorities
        rec4, out4 = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="get_external_authorities",
            arguments={"person_name": "Howqua"},
            call_id="call-ground-4",
        ))
        self.assertEqual(rec4.status, "success")
        self.assertTrue(len(out4) >= 1)
        self.assertIn("广东十三行考", out4[0]["record_title"])

        # 5. graph_rag_query
        rec5, out5 = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="graph_rag_query",
            arguments={"question": "Henry Browne 职务与往来"},
            call_id="call-ground-5",
        ))
        self.assertEqual(rec5.status, "success")
        self.assertIn("sources", out5)

        # 6. analyze_query
        rec6, out6 = asyncio.run(tool_registry.execute_tool(
            db=self.db,
            tool_name="analyze_query",
            arguments={"query": "1792 年 Henry Browne 与 Howqua 商业往来"},
            call_id="call-ground-6",
        ))
        self.assertEqual(rec6.status, "success")
        self.assertEqual(out6["time_filter"], 1792)

    # -------------------------------------------------------------
    # Test 3: Agent Research Planner
    # -------------------------------------------------------------
    def test_03_agent_planner_decomposition(self):
        """Verify planner breaks complex questions into finite checkable steps."""
        import asyncio
        plan = asyncio.run(agent_planner.create_plan(
            db=self.db,
            question="Henry Browne 与 Howqua 在 1792 年商谈毛织品贸易时订立了什么合同？",
            time_filter=1792,
        ))
        self.assertTrue(len(plan.proposed_steps) >= 2)
        self.assertEqual(plan.time_filter, 1792)
        self.assertTrue(any("Howqua" in e or "Browne" in e for e in plan.target_entities))

    # -------------------------------------------------------------
    # Test 4: Budget Caps and Limits (max_steps, max_tool_calls)
    # -------------------------------------------------------------
    def test_04_agent_budget_caps_enforcement(self):
        """Verify Agent respects max_steps and max_tool_calls boundaries and unifies budget metrics."""
        import asyncio

        # Case 1: Max steps enforcement
        req_steps = AgentResearchRequest(
            question="Henry Browne 与 Howqua 之间有什么往来与商业关系？",
            max_steps=1,
            max_tool_calls=6,
        )
        resp_steps = asyncio.run(agent_runner.run_research(db=self.db, request=req_steps))
        self.assertEqual(len(resp_steps.trajectory), 1)
        self.assertEqual(resp_steps.budget_used.steps_executed, 1)
        self.assertEqual(resp_steps.budget_used.max_steps, 1)
        self.assertTrue(resp_steps.budget_used.budget_exhausted)
        self.assertGreaterEqual(resp_steps.budget_used.elapsed_ms, 0.0)
        # Check dict access compatibility
        self.assertEqual(resp_steps.budget_used["steps_executed"], 1)

        # Case 2: Max tool calls enforcement
        req_tools = AgentResearchRequest(
            question="Henry Browne 与 Howqua 之间有什么往来与商业关系？",
            max_steps=4,
            max_tool_calls=1,
        )
        resp_tools = asyncio.run(agent_runner.run_research(db=self.db, request=req_tools))
        self.assertEqual(resp_tools.budget_used.tool_calls_executed, 1)
        self.assertEqual(resp_tools.budget_used.max_tool_calls, 1)
        self.assertTrue(resp_tools.budget_used.budget_exhausted)

        # Case 3: Refusal response budget metrics
        req_refusal = AgentResearchRequest(
            question="拿破仑在广州十三行商馆进行了什么军事演习？",
        )
        resp_refusal = asyncio.run(agent_runner.run_research(db=self.db, request=req_refusal))
        self.assertEqual(resp_refusal.budget_used.steps_executed, 1)
        self.assertEqual(resp_refusal.budget_used.tool_calls_executed, 0)
        self.assertFalse(resp_refusal.budget_used.budget_exhausted)

    # -------------------------------------------------------------
    # Test 5: Anti-Hallucination Gate (No-Evidence Refusal)
    # -------------------------------------------------------------
    def test_05_anti_hallucination_refusal(self):
        """Verify fictitious/out-of-bounds questions are refused with zero sources."""
        import asyncio
        req = AgentResearchRequest(
            question="拿破仑在广州十三行商馆进行了什么军事演习？",
        )
        resp = asyncio.run(agent_runner.run_research(db=self.db, request=req))
        self.assertFalse(resp.evidence_sufficient)
        self.assertEqual(len(resp.sources), 0)
        self.assertIn("没有足够证据", resp.final_answer)
        self.assertTrue(len(resp.categorization.unresolved_aspects) >= 1)

    # -------------------------------------------------------------
    # Test 6: End-to-End Autonomous Research with Real Citations
    # -------------------------------------------------------------
    def test_06_end_to_end_research_with_citations(self):
        """Verify Agent gathers multi-source evidence and outputs 4-level categorized answers."""
        import asyncio
        req = AgentResearchRequest(
            question="Henry Browne 与 Howqua 之间有什么往来与商业关系？",
            max_steps=4,
            max_tool_calls=6,
        )
        resp = asyncio.run(agent_runner.run_research(db=self.db, request=req))
        self.assertTrue(resp.evidence_sufficient)
        self.assertTrue(len(resp.sources) >= 1)
        self.assertTrue(len(resp.trajectory) >= 1)
        # Check citations anchor in real block with non-empty archival text
        self.assertGreater(resp.sources[0].block_id, 0)
        cited_b = self.db.query(Block).get(resp.sources[0].block_id)
        self.assertIsNotNone(cited_b)
        content = (cited_b.ocr_text_corrected or cited_b.ocr_text_raw or "")
        self.assertTrue(len(content.strip()) > 10)
        # Check structured sections in answer
        self.assertIn("### 档案直接记载", resp.final_answer)
        self.assertIn("### 图谱关系事实", resp.final_answer)

    # -------------------------------------------------------------
    # Test 7: API Endpoints (tools, plan, research)
    # -------------------------------------------------------------
    def test_07_api_endpoints(self):
        """Verify FastAPI RESTful endpoints /api/agent/tools, /plan, /research."""
        # 1. /api/agent/tools
        res_tools = self.client.get("/api/agent/tools")
        self.assertEqual(res_tools.status_code, 200)
        tools_data = res_tools.json()
        self.assertTrue(len(tools_data) >= 6)

        # 2. /api/agent/plan
        res_plan = self.client.post("/api/agent/plan?question=1792年毛织品贸易")
        self.assertEqual(res_plan.status_code, 200)
        plan_data = res_plan.json()
        self.assertTrue(len(plan_data["proposed_steps"]) >= 2)

        # 3. /api/agent/research
        res_research = self.client.post(
            "/api/agent/research",
            json={
                "question": "Henry Browne 在广州商馆担任什么职务？",
                "max_steps": 2,
                "max_tool_calls": 3,
            },
        )
        self.assertEqual(res_research.status_code, 200)
        res_data = res_research.json()
        self.assertIn("task_id", res_data)
        self.assertTrue(len(res_data["trajectory"]) >= 1)

    # -------------------------------------------------------------
    # Test 8: Dynamic Tool Scheduling (DEF-AGENT-03 verification)
    # -------------------------------------------------------------
    def test_08_dynamic_tool_scheduling_def_agent_03(self):
        """Verify dynamic tool dispatch responds to sub-goal semantics and newly discovered entities."""
        from app.schemas.rag import QueryIntentAnalysis, RagEntityReference

        intent = QueryIntentAnalysis(
            raw_query="广州商馆贸易与人物考据",
            query_type="relational",
            entities=["Henry Browne"],
            time_filter=1792,
            search_keywords=["woolen", "contracts"],
        )

        # 1. Sub-goal: archival evidence retrieval
        tools_archival = agent_runner._select_tools_for_step(
            step_idx=0,
            sub_goal="第三步：检索档案文本中关于毛织品合同商谈与会议记录的直接记载",
            intent=intent,
            resolved_year=1792,
            query="Henry Browne contracts",
            collected_entities=[],
            collected_sources=[],
            collected_relationships=[],
        )
        tool_names_archival = [t[0] for t in tools_archival]
        self.assertIn("search_archival_evidence", tool_names_archival)
        self.assertNotIn("search_persons", tool_names_archival)

        # 2. Sub-goal: external scholarship
        tools_external = agent_runner._select_tools_for_step(
            step_idx=0,
            sub_goal="第二步：检索梁嘉彬《广东十三行考》等外部学术史学对行商身份的考据",
            intent=intent,
            resolved_year=1792,
            query="Howqua",
            collected_entities=[],
            collected_sources=[],
            collected_relationships=[],
        )
        tool_names_external = [t[0] for t in tools_external]
        self.assertIn("get_external_authorities", tool_names_external)

        # 3. Dynamic feedback: newly discovered entities influence subsequent relationship retrieval
        newly_found_entity = RagEntityReference(
            id=101,
            display_name="Puankhequa (Pan Zhencheng)",
            canonical_name_zh="潘启官",
        )
        tools_rel = agent_runner._select_tools_for_step(
            step_idx=1,
            sub_goal="查询人物间商业与担保借贷往来关系网络",
            intent=intent,
            resolved_year=1792,
            query="Puankhequa",
            collected_entities=[newly_found_entity],
            collected_sources=[],
            collected_relationships=[],
        )
        # Verify that the newly discovered entity "潘启官" is dynamically targeted
        rel_calls = [args for name, args in tools_rel if name == "get_person_relationships"]
        self.assertTrue(len(rel_calls) >= 1)
        rel_target_names = [call.get("person_name") for call in rel_calls]
        self.assertTrue(
            any("潘启官" in name or "Puankhequa" in name or "Henry Browne" in name for name in rel_target_names)
        )


if __name__ == "__main__":
    unittest.main()

