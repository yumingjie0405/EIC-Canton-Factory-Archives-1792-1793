"""Regression and Unit Test Suite for Stage 8 Phase 2 Remediation:
- DEF-STAGE8-01: Ghost blank block citation elimination & primary source verification
- DEF-STAGE8-02: Cross-era temporal leak prevention & document metadata binding
"""
import unittest
from unittest.mock import AsyncMock, patch
from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.test_support.db_fixtures import (
    drop_test_schema,
    make_session_factory,
    make_test_engine,
)
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.models.kg import Person, Relationship, RelationshipEvidence
from app.models.kg.enums import (
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    RelationType,
    ReviewStatusKG,
)
from app.schemas.rag import QueryIntentAnalysis, RagRelationshipReference, RagSourceEvidence
from app.services.agent.agent_runner import agent_runner
from app.services.agent.tool_registry import tool_registry
from app.services.rag.temporal_resolver import temporal_resolver
from app.services.rag.vector_retriever import vector_retriever


class TestStage8Remediation(unittest.TestCase):
    """Test suite covering DEF-STAGE8-01 and DEF-STAGE8-02 remediation."""

    @classmethod
    def setUpClass(cls):
        # Test isolation: in-memory SQLite created through the fail-closed guard.
        cls.test_engine = make_test_engine("sqlite:///:memory:")
        cls.TestSession = make_session_factory(cls.test_engine)
        cls.db = cls.TestSession()
        cls._populate_test_data()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        drop_test_schema(cls.test_engine)

    @classmethod
    def _populate_test_data(cls):
        # 1. Document #4: 1779 Consultations (Verified 1779)
        doc_1779 = Document(
            id=4,
            filename="doc_1779.pdf",
            original_filename="China and Japan Canton Consultations and Letter Book, 9 Mar - 26 Oct 1779.pdf",
            file_path="/storage/doc_1779.pdf",
            file_type=DocumentType.PDF,
            file_size=1024,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(doc_1779)
        cls.db.flush()

        page_1779_p3 = Page(
            id=3,
            document_id=doc_1779.id,
            page_number=3,
            image_path="/storage/page_3.png",
            width=1000,
            height=1500,
        )
        page_1779_p8 = Page(
            id=8,
            document_id=doc_1779.id,
            page_number=8,
            image_path="/storage/page_8.png",
            width=1000,
            height=1500,
        )
        cls.db.add_all([page_1779_p3, page_1779_p8])
        cls.db.flush()

        # Block #2: The ghost blank block on Page 3 (copyright/target slip)
        block_2_empty = Block(
            id=2,
            page_id=page_1779_p3.id,
            block_index=1,
            block_label="text",
            bbox=[100.0, 100.0, 300.0, 200.0],
            ocr_text_raw="",
            ocr_text_corrected=None,
            modern_english=None,
        )
        # Block #34: A valid block with name on Page 8
        block_34_valid = Block(
            id=34,
            page_id=page_1779_p8.id,
            block_index=1,
            block_label="text",
            bbox=[100.0, 200.0, 400.0, 300.0],
            ocr_text_raw="Henry Browne Esqr attended the meeting with Chinese merchants.",
            ocr_text_corrected="Henry Browne Esqr attended the meeting with Chinese merchants.",
            modern_english="Henry Browne Esq attended the meeting with Chinese merchants.",
        )
        # Block #585: A block in 1779 Document #4 that happens to mention "1792" in debt context
        block_585_1779 = Block(
            id=585,
            page_id=page_1779_p8.id,
            block_index=2,
            block_label="text",
            bbox=[100.0, 300.0, 400.0, 400.0],
            ocr_text_raw="Interest on D'at 1792 settled with Mowqua by Canton council.",
            ocr_text_corrected="Interest on D'at 1792 settled with Mowqua by Canton council.",
            modern_english="Interest on debt 1792 settled with Mowqua by Canton council.",
        )
        cls.db.add_all([block_2_empty, block_34_valid, block_585_1779])

        # 2. Document #54: 1791-1792 Consultations (Spanning 1791-1792)
        doc_1791_1792 = Document(
            id=54,
            filename="doc_1791_1792.pdf",
            original_filename="China and Japan Canton Consultations, 1791-1792.pdf",
            file_path="/storage/doc_1791_1792.pdf",
            file_type=DocumentType.PDF,
            file_size=2048,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(doc_1791_1792)
        cls.db.flush()

        page_1791_1792 = Page(
            id=5401,
            document_id=doc_1791_1792.id,
            page_number=5,
            image_path="/storage/page_5401.png",
            width=1000,
            height=1500,
        )
        cls.db.add(page_1791_1792)
        cls.db.flush()

        block_1791_1792 = Block(
            id=54011,
            page_id=page_1791_1792.id,
            block_index=1,
            block_label="text",
            bbox=[50.0, 50.0, 500.0, 300.0],
            ocr_text_raw="Negotiations in late 1791 and early 1792 concerning woollens quotas.",
            ocr_text_corrected="Negotiations in late 1791 and early 1792 concerning woollens quotas.",
            modern_english="Negotiations in late 1791 and early 1792 concerning woollens quotas.",
        )
        cls.db.add(block_1791_1792)

        # 3. Document #55: 1792-1793 Consultations (Verified 1792-1793)
        doc_1792 = Document(
            id=55,
            filename="doc_1792.pdf",
            original_filename="China and Japan Canton Consultations, 15 Jan 1792 - 15 Mar 1793.pdf",
            file_path="/storage/doc_1792.pdf",
            file_type=DocumentType.PDF,
            file_size=2048,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(doc_1792)
        cls.db.flush()

        page_1792 = Page(
            id=5501,
            document_id=doc_1792.id,
            page_number=12,
            image_path="/storage/page_5501.png",
            width=1000,
            height=1500,
        )
        cls.db.add(page_1792)
        cls.db.flush()

        block_1792 = Block(
            id=55011,
            page_id=page_1792.id,
            block_index=1,
            block_label="text",
            bbox=[50.0, 50.0, 500.0, 300.0],
            ocr_text_raw="In 1792, the Select Committee settled the contracts for tea and woollens.",
            ocr_text_corrected="In 1792, the Select Committee settled the contracts for tea and woollens.",
            modern_english="In 1792, the Select Committee settled the contracts for tea and woollens.",
        )
        cls.db.add(block_1792)

        # 4. Document #999: Unknown year document
        doc_unknown = Document(
            id=999,
            filename="undated_notes.pdf",
            original_filename="Miscellaneous undated notes without year in title.pdf",
            file_path="/storage/undated.pdf",
            file_type=DocumentType.PDF,
            file_size=512,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(doc_unknown)
        cls.db.flush()

        page_unknown = Page(
            id=9991,
            document_id=doc_unknown.id,
            page_number=1,
            image_path="/storage/page_9991.png",
            width=1000,
            height=1500,
        )
        cls.db.add(page_unknown)
        cls.db.flush()

        block_unknown = Block(
            id=99911,
            page_id=page_unknown.id,
            block_index=1,
            block_label="text",
            bbox=[10.0, 10.0, 200.0, 200.0],
            ocr_text_raw="Undated general ledger fragment concerning port dues.",
            ocr_text_corrected="Undated general ledger fragment concerning port dues.",
            modern_english="Undated general ledger fragment concerning port dues.",
        )
        # Block #99912: A block in undated doc #999 that happens to mention "1792"
        block_unknown_1792 = Block(
            id=99912,
            page_id=page_unknown.id,
            block_index=2,
            block_label="text",
            bbox=[10.0, 210.0, 200.0, 300.0],
            ocr_text_raw="In 1792 customs ledger entry regarding anchorage duty in Whampoa.",
            ocr_text_corrected="In 1792 customs ledger entry regarding anchorage duty in Whampoa.",
            modern_english="In 1792 customs ledger entry regarding anchorage duty in Whampoa.",
        )
        cls.db.add_all([block_unknown, block_unknown_1792])

        # 4. Persons & Relationships
        p_browne = Person(
            id=405,
            canonical_name_en="Henry Browne",
            canonical_name_zh="亨利·布朗",
            display_name="Henry Browne",
            primary_role="Supercargo",
            identity_status=IdentityStatus.ACCEPTED,
        )
        p_howqua = Person(
            id=404,
            canonical_name_en="Wu Bingjian",
            canonical_name_zh="伍秉鉴",
            display_name="Howqua",
            primary_role="Security Merchant",
            identity_status=IdentityStatus.ACCEPTED,
        )
        cls.db.add_all([p_browne, p_howqua])
        cls.db.flush()

        # Relationship pointing to empty block 2
        rel_ghost = Relationship(
            id=114,
            source_person_id=p_browne.id,
            target_person_id=p_howqua.id,
            relation_type=RelationType.COMMERCIAL,
            confidence=0.95,
            time_description="1792",
            evidence_summary="Henry Browne and Howqua woolen contract",
            claim_status=ClaimStatus.ACCEPTED,
            review_status=ReviewStatusKG.REVIEWED,
        )
        cls.db.add(rel_ghost)
        cls.db.flush()

        ev_ghost = RelationshipEvidence(
            id=102,
            relationship_id=rel_ghost.id,
            block_id=2,  # Points to empty block 2
            evidence_role=EvidenceRole.SUPPORTS,
            quote_text="At a Consultation: Present Henry Browne Esqr... Howqua attended and settled woolen contracts.",
        )
        cls.db.add(ev_ghost)

        # Relationship pointing to valid block 34
        rel_valid = Relationship(
            id=115,
            source_person_id=p_browne.id,
            target_person_id=p_howqua.id,
            relation_type=RelationType.COMMERCIAL,
            confidence=0.90,
            time_description="1779",
            evidence_summary="Henry Browne attended meeting",
            claim_status=ClaimStatus.ACCEPTED,
            review_status=ReviewStatusKG.REVIEWED,
        )
        cls.db.add(rel_valid)
        cls.db.flush()

        ev_valid = RelationshipEvidence(
            id=103,
            relationship_id=rel_valid.id,
            block_id=34,  # Points to valid block 34
            evidence_role=EvidenceRole.SUPPORTS,
            quote_text="Henry Browne Esqr attended the meeting with Chinese merchants.",
        )
        cls.db.add(ev_valid)

        cls.db.commit()

    # =========================================================================
    # Test 1: 关系记录引用空白 Block，必须拒绝作为直接引文 (DEF-STAGE8-01)
    # =========================================================================
    def test_01_relationship_evidence_empty_block_rejected_as_primary(self):
        """When a relationship points to a block with empty text (like Block 2), it must be rejected as primary citation."""
        results = tool_registry._tool_get_person_relationships(self.db, person_name="Henry Browne")
        rel_114 = [r for r in results if r["relationship_id"] == 114][0]

        # In tool output: is_valid_primary_evidence must be False because Block #2 text is empty
        self.assertFalse(rel_114["is_valid_primary_evidence"])
        self.assertIsNone(rel_114["verified_quote_text"])

        # In Agent accumulation: must NOT add to sources
        sources = []
        relationships = []
        seen_blocks = set()

        agent_runner._accumulate_tool_results(
            tool_name="get_person_relationships",
            output=[rel_114],
            sources=sources,
            entities=[],
            relationships=relationships,
            seen_blocks=seen_blocks,
        )

        # Asserts
        self.assertEqual(len(sources), 0, "Empty block must NEVER be injected into sources as direct primary citation")
        self.assertEqual(len(relationships), 1, "Relationship metadata must still be preserved in KG facts")
        self.assertEqual(relationships[0].review_status, "needs_verification_empty_block")

    # =========================================================================
    # Test 2: 关系记录引用真实且文字匹配的 Block，正常纳入并绑定一致元数据 (DEF-STAGE8-01)
    # =========================================================================
    def test_02_relationship_evidence_valid_block_accepted(self):
        """When a relationship points to a valid block (Block 34), it should be verified and included with exact metadata."""
        results = tool_registry._tool_get_person_relationships(self.db, person_name="Henry Browne")
        rel_115 = [r for r in results if r["relationship_id"] == 115][0]

        self.assertTrue(rel_115["is_valid_primary_evidence"])
        self.assertIsNotNone(rel_115["verified_quote_text"])
        self.assertEqual(rel_115["document_id"], 4)
        self.assertEqual(rel_115["page_number"], 8)
        self.assertIn("1779", rel_115["document_name"])

        # Accumulate
        sources = []
        relationships = []
        seen_blocks = set()

        agent_runner._accumulate_tool_results(
            tool_name="get_person_relationships",
            output=[rel_115],
            sources=sources,
            entities=[],
            relationships=relationships,
            seen_blocks=seen_blocks,
        )

        self.assertEqual(len(sources), 1)
        src = sources[0]
        self.assertEqual(src.block_id, 34)
        self.assertEqual(src.document_id, 4)
        self.assertEqual(src.page_number, 8)
        self.assertIn("Henry Browne", src.quote_text)
        self.assertEqual(src.evidence_type, "primary_archival")

    # =========================================================================
    # Test 3: 引文文字与 OCR 原文不匹配时，禁止用自定义字符串冒充 OCR 原文
    # =========================================================================
    def test_03_quote_text_mismatch_with_ocr_rejected_as_direct_quote(self):
        """The relationship table's artificial quote must not replace the verified block text."""
        results = tool_registry._tool_get_person_relationships(self.db, person_name="Henry Browne")
        rel_114 = [r for r in results if r["relationship_id"] == 114][0]

        # The table had artificial text about woolen contracts, but the block was empty
        self.assertIn("wool", (rel_114.get("quote_text") or "").lower())
        self.assertIsNone(rel_114["verified_quote_text"], "Verified text must be None for empty block")

    # =========================================================================
    # Test 4: document_id, page_number 与 block_id 来自同一真实关联链 (DEF-STAGE8-01)
    # =========================================================================
    def test_04_document_page_block_chain_consistency(self):
        """Verify that document_id, page_number, and block_id in citations come from the exact same DB row chain."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="Select Committee tea woollens 1792",
            keywords=["contracts"],
            entities=["Select Committee"],
            top_k=2,
        )
        self.assertGreater(len(evidences), 0)
        for ev in evidences:
            # Query block from DB and check foreign keys
            b = self.db.query(Block).filter(Block.id == ev.block_id).first()
            self.assertIsNotNone(b)
            p = self.db.query(Page).filter(Page.id == b.page_id).first()
            self.assertIsNotNone(p)
            self.assertEqual(ev.page_number, p.page_number)
            self.assertEqual(ev.document_id, p.document_id)

    # =========================================================================
    # Test 5: 1792 年问题检索硬隔离，不得召回 1779 年材料 (DEF-STAGE8-02)
    # =========================================================================
    def test_05_1792_query_excludes_1779_document_materials(self):
        """When query is restricted to 1792, documents from 1779 (Doc #4) must be strictly pruned."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="Henry Browne merchant meeting",
            keywords=["Browne", "meeting"],
            entities=["Henry Browne"],
            top_k=5,
            time_filter=1792,
        )
        # Block 34 (Henry Browne meeting) is in Doc #4 (1779). It MUST NOT appear in 1792-filtered retrieval!
        doc_ids = [ev.document_id for ev in evidences]
        self.assertNotIn(4, doc_ids, "1779 Document #4 must NEVER be retrieved when time_filter=1792")

    # =========================================================================
    # Test 6: 问题没有明确年代时，正常召回但附带明确年代元数据 (DEF-STAGE8-02)
    # =========================================================================
    def test_06_query_without_explicit_year(self):
        """When query has no year constraint, all relevant documents are retrieved with temporal tags."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="meeting with Chinese merchants",
            keywords=["merchants"],
            entities=[],
            top_k=5,
            time_filter=None,
        )
        self.assertGreater(len(evidences), 0)
        # Check that temporal_coverage is populated
        for ev in evidences:
            self.assertIsNotNone(ev.temporal_coverage)
            self.assertIsNotNone(ev.temporal_provenance)

    # =========================================================================
    # Test 7: 文档年代未知时，不标记为确定值，且不满足特定年代约束 (DEF-STAGE8-02)
    # =========================================================================
    def test_07_document_with_unknown_year(self):
        """Documents with unknown year must have is_certain=False and must not satisfy is_document_covering_year."""
        t_info = temporal_resolver.resolve_document_temporal_info(999, "Miscellaneous undated notes.pdf")
        self.assertFalse(t_info["is_certain"])
        self.assertEqual(t_info["provenance"], "unknown")
        self.assertEqual(t_info["temporal_label"], "年代未确定")

        # Unknown document should not be assumed to cover 1792
        covers_1792 = temporal_resolver.is_document_covering_year(999, 1792, "Miscellaneous undated notes.pdf")
        self.assertFalse(covers_1792, "Unknown document must not satisfy specific year 1792 constraint")

    # =========================================================================
    # Test 8: 合法的跨年代比较问题，允许召回多年代并保留各自年代标记 (DEF-STAGE8-02)
    # =========================================================================
    def test_08_legitimate_cross_era_comparison_query(self):
        """Cross-era comparison query must be recognized and allow multi-era evidence."""
        query = "比较 1779 年与 1792 年广州商馆会议记录中大班出席与议程的变化"
        is_cross = temporal_resolver.is_cross_era_query(query)
        self.assertTrue(is_cross, "Query comparing 1779 and 1792 must be detected as cross-era")

        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query=query,
            keywords=["meeting", "contracts"],
            entities=[],
            top_k=5,
            time_filter=1792,  # Even if analyzer guessed 1792, cross-era detection relaxes hard pruning
        )
        # Should be able to retrieve both 1779 and 1792 materials without crashing
        self.assertGreater(len(evidences), 0)

    # =========================================================================
    # REF-STAGE8-BE01 核心场景复验（6大要求场景）
    # =========================================================================
    def test_09_scenario1_1779_doc_with_1792_in_ocr_text_excluded_from_strict_1792_query(self):
        """Scenario 1: 1779 Document (#4) has '1792' in OCR text, but must NEVER enter direct evidence in strict 1792 query."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="1792 年广州商馆关于债务利息的清偿记录 (debt interest settled with Mowqua)",
            keywords=["debt", "interest", "Mowqua"],
            entities=["Mowqua"],
            top_k=5,
        )
        doc_ids = [ev.document_id for ev in evidences]
        # Doc 4 is a 1779 document. Even though block 585 has "1792", it MUST NOT enter direct evidence!
        self.assertNotIn(
            4,
            doc_ids,
            "1779 年文档 #4 仅因正文出现 1792 字样，绝不能进入严格 1792 年检索的直接证据！",
        )

    def test_10_scenario2_unknown_doc_with_1792_in_ocr_text_excluded_from_strict_1792_query(self):
        """Scenario 2: Unknown-year Document (#999) has '1792' in OCR text, but must NEVER enter direct evidence in strict 1792 query."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="1792 年黄埔港口停泊税记录 (Whampoa anchorage duty customs)",
            keywords=["anchorage", "duty", "Whampoa"],
            entities=[],
            top_k=5,
        )
        doc_ids = [ev.document_id for ev in evidences]
        # Doc 999 is unknown year. In strict single-year 1792 query, it MUST NOT enter direct evidence!
        self.assertNotIn(
            999,
            doc_ids,
            "年代未知文档 #999 即使正文出现 1792，在严格 1792 年检索下也绝不能伪造确凿年代进入直接证据！",
        )

    def test_11_scenario3_1791_1792_spanning_doc_included_in_strict_1792_query(self):
        """Scenario 3: 1791-1792 spanning Document (#54) legitimately covers 1792 and MUST be included."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="1792 年毛织品配额谈判 (woollens quotas negotiations)",
            keywords=["woollens", "quotas", "negotiations"],
            entities=[],
            top_k=5,
        )
        doc_ids = [ev.document_id for ev in evidences]
        self.assertIn(
            54,
            doc_ids,
            "1791–1792 跨年卷宗 #54 涵盖 1792 年，必须能正常进入严格 1792 年检索候选池！",
        )
        ev_54 = next(ev for ev in evidences if ev.document_id == 54)
        self.assertIn("1791–1792年", ev_54.temporal_coverage)
        self.assertEqual(ev_54.temporal_provenance, "human_verified")

    def test_12_scenario4_1792_1793_spanning_doc_included_in_strict_1792_query(self):
        """Scenario 4: 1792-1793 spanning Document (#55) legitimately covers 1792 and MUST be included."""
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query="1792 年茶叶与毛织品合同订立 (contracts tea woollens Select Committee)",
            keywords=["tea", "woollens", "contracts"],
            entities=["Select Committee"],
            top_k=5,
        )
        doc_ids = [ev.document_id for ev in evidences]
        self.assertIn(
            55,
            doc_ids,
            "1792–1793 跨年卷宗 #55 涵盖 1792 年，必须能正常进入严格 1792 年检索候选池！",
        )
        ev_55 = next(ev for ev in evidences if ev.document_id == 55)
        self.assertIn("1792–1793年", ev_55.temporal_coverage)
        self.assertEqual(ev_55.temporal_provenance, "human_verified")

    def test_13_scenario5_legitimate_1779_and_1792_cross_era_comparison(self):
        """Scenario 5: Legitimate cross-era comparison query between 1779 and 1792 recalls both eras."""
        query = "比较 1779 年与 1792 年广州商馆大班出席与商贸合同制度的演变"
        self.assertTrue(temporal_resolver.is_cross_era_query(query))
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query=query,
            keywords=["meeting", "contracts", "Browne"],
            entities=["Henry Browne"],
            top_k=10,
        )
        doc_ids = set(ev.document_id for ev in evidences)
        # Should contain 1779 Document (#4) AND 1792 Document (#55 or #54)
        self.assertIn(4, doc_ids, "跨年代比较必须能够合法召回 1779 年历史文档")
        self.assertTrue(54 in doc_ids or 55 in doc_ids, "跨年代比较必须能够合法召回 1792 目标年代文档")
        # Ensure each evidence retains its true temporal label
        for ev in evidences:
            if ev.document_id == 4:
                self.assertIn("1779年", ev.temporal_coverage)
            elif ev.document_id == 55:
                self.assertIn("1792–1793年", ev.temporal_coverage)
            elif ev.document_id == 54:
                self.assertIn("1791–1792年", ev.temporal_coverage)

    def test_14_scenario6_undated_exploratory_query_preserves_temporal_provenance_and_uncertainty(self):
        """Scenario 6: Undated exploratory query allows documents but preserves unknown tags without faking certainty."""
        query = "广州商馆关于港口停泊规费与商人会议的记录 (port dues anchorage duty meeting)"
        detected_years = temporal_resolver.detect_query_years(query)
        self.assertEqual(len(detected_years), 0, "探索性查询不含明确年份")
        evidences = vector_retriever.retrieve_text_evidence(
            db=self.db,
            query=query,
            keywords=["port", "dues", "anchorage", "meeting"],
            entities=[],
            top_k=10,
            time_filter=None,
        )
        self.assertGreater(len(evidences), 0)
        # Verify unknown-year document maintains its honest uncertainty tag
        unknown_evs = [ev for ev in evidences if ev.document_id == 999]
        if unknown_evs:
            for ev in unknown_evs:
                self.assertEqual(ev.temporal_provenance, "unknown")
                self.assertIn("年代未确定", ev.temporal_coverage)


if __name__ == "__main__":
    unittest.main()
