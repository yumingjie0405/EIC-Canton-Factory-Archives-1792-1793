"""Comprehensive test suite for Stage 2 Knowledge Graph extraction pipeline."""
import asyncio
from datetime import date
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.test_support.db_fixtures import (
    drop_test_schema,
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)
from app.models.document import Document, DocumentType, DocumentStatus
from app.models.page import Page
from app.models.block import Block, ReviewStatus as BlockReviewStatus
from app.models.kg import (
    Directionality,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    Person,
    PersonMention,
    RelationType,
    Relationship,
    RelationshipEvidence,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.schemas.kg_extraction_schema import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionTaskMetrics,
    KGExtractionResult,
)
from app.services.kg.relation_parser import relation_parser, RelationParserError
from app.services.kg.kg_writer import kg_writer
from app.services.kg.extraction_service import extraction_service


@requires_postgres_test_db
class TestKGExtraction(unittest.TestCase):
    """Test suite covering Schema validation, Parser resilience, Writer persistence, and Service flow."""

    @classmethod
    def setUpClass(cls):
        # Test isolation: dedicated test database only (guard validated).
        cls.engine = make_test_engine()
        cls.session_factory = make_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        drop_test_schema(cls.engine)

    def setUp(self):
        """Setup isolated test fixtures in database."""
        self.db = self.session_factory()

        self.test_doc = Document(
            filename="canton_secret_consultations_1792.pdf",
            original_filename="canton_secret_consultations_1792.pdf",
            file_path="storage/uploads/canton_1792.pdf",
            file_type=DocumentType.PDF,
            file_size=2048,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        self.db.add(self.test_doc)
        self.db.commit()

        self.test_page = Page(
            document_id=self.test_doc.id,
            page_number=1,
            image_path="storage/pages/test_p1.png",
            width=1200,
            height=1600,
        )
        self.db.add(self.test_page)
        self.db.commit()

        self.test_block = Block(
            page_id=self.test_page.id,
            block_index=1,
            block_label="paragraph",
            bbox=[50, 100, 750, 220],
            ocr_text_raw="Howqua and Supercargo Browne met at the British Factory regarding the security of the ship Royal Charlotte.",
            ocr_text_corrected="Howqua and Supercargo Browne met at the British Factory regarding the security of the ship Royal Charlotte.",
            review_status=BlockReviewStatus.APPROVED,
        )
        self.db.add(self.test_block)
        self.db.commit()

    def tearDown(self):
        """Clean up test fixtures."""
        try:
            self.db.rollback()
            self.db.query(RelationshipEvidence).delete()
            self.db.query(Relationship).delete()
            self.db.query(PersonMention).delete()
            self.db.query(Person).delete()

            if hasattr(self, "test_block") and self.test_block:
                self.db.query(Block).filter_by(id=self.test_block.id).delete()
            if hasattr(self, "test_page") and self.test_page:
                self.db.query(Page).filter_by(id=self.test_page.id).delete()
            if hasattr(self, "test_doc") and self.test_doc:
                self.db.query(Document).filter_by(id=self.test_doc.id).delete()

            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self.db.close()

    # ----------------------------------------------------
    # 1. Extraction Schema Validation Tests
    # ----------------------------------------------------

    def test_extracted_entity_valid(self):
        """Valid ExtractedEntity parses correctly."""
        ent = ExtractedEntity(
            mention="  Howqua  ",
            normalized_name="Howqua",
            role="Security Merchant",
            organization="EIC Cohong",
            confidence=0.95,
            evidence="Howqua met Browne",
        )
        self.assertEqual(ent.mention, "Howqua")
        self.assertEqual(ent.confidence, 0.95)

    def test_extracted_entity_empty_mention_fails(self):
        """Blank or empty mention fails validation."""
        with self.assertRaises(ValidationError):
            ExtractedEntity(mention="   ")

    def test_extracted_entity_confidence_bounds(self):
        """Confidence outside [0, 1] fails validation."""
        with self.assertRaises(ValidationError):
            ExtractedEntity(mention="Browne", confidence=1.2)
        with self.assertRaises(ValidationError):
            ExtractedEntity(mention="Browne", confidence=-0.1)

    def test_extracted_relationship_normalization(self):
        """Relationship type normalizes to controlled vocabulary."""
        rel = ExtractedRelationship(
            source_mention="Howqua",
            target_mention="Browne",
            relation_type="GUARANTEE",
            confidence=0.9,
        )
        self.assertEqual(rel.relation_type, "guarantee")

        # Unknown relation type defaults to 'other'
        rel2 = ExtractedRelationship(
            source_mention="Howqua",
            target_mention="Browne",
            relation_type="magical_bond",
            confidence=0.8,
        )
        self.assertEqual(rel2.relation_type, "other")

    # ----------------------------------------------------
    # 2. RelationParser JSON Parsing Tests
    # ----------------------------------------------------

    def test_parser_strips_markdown_fences(self):
        """Parser extracts JSON successfully from markdown fences."""
        raw_json_with_fences = """```json
        {
          "entities": [
            {
              "mention": "Mr. Browne",
              "normalized_name": "Browne",
              "role": "Supercargo",
              "confidence": 0.92,
              "evidence": "Mr. Browne received the petition."
            }
          ],
          "relationships": [],
          "temporal_date_hint": "1792-10-24",
          "spatial_location_hint": "Canton"
        }
        ```"""
        result = relation_parser.parse_extraction_json(raw_json_with_fences)
        self.assertEqual(len(result.entities), 1)
        self.assertEqual(result.entities[0].mention, "Mr. Browne")
        self.assertEqual(result.entities[0].normalized_name, "Browne")
        self.assertEqual(result.temporal_date_hint, "1792-10-24")
        self.assertEqual(result.spatial_location_hint, "Canton")

    def test_parser_handles_preceding_and_trailing_text(self):
        """Parser locates JSON object even when surrounded by extraneous commentary."""
        raw_output = """Here is the extracted information:
        {
          "entities": [{"mention": "Lord Macartney", "role": "Ambassador", "confidence": 0.98}],
          "relationships": []
        }
        Hope this helps!"""
        result = relation_parser.parse_extraction_json(raw_output)
        self.assertEqual(len(result.entities), 1)
        self.assertEqual(result.entities[0].mention, "Lord Macartney")

    def test_parser_invalid_json_raises_error(self):
        """Completely invalid non-JSON output raises RelationParserError."""
        with self.assertRaises(RelationParserError):
            relation_parser.parse_extraction_json("Not a json string at all.")

    # ----------------------------------------------------
    # 3. Database Persistence & Idempotency Tests
    # ----------------------------------------------------

    def test_writer_persists_mentions_and_relationships(self):
        """Writer stores PersonMention (unresolved) and Relationship linked to evidence."""
        sample_result = KGExtractionResult(
            entities=[
                ExtractedEntity(
                    mention="Howqua",
                    normalized_name="Howqua",
                    role="Security Merchant",
                    confidence=0.95,
                    evidence="Howqua and Supercargo Browne met...",
                ),
                ExtractedEntity(
                    mention="Browne",
                    normalized_name="Browne",
                    role="President of Select Committee",
                    confidence=0.90,
                    evidence="...Browne met at the British Factory...",
                ),
            ],
            relationships=[
                ExtractedRelationship(
                    source_mention="Howqua",
                    target_mention="Browne",
                    relation_type="guarantee",
                    relation_label="Security bond",
                    confidence=0.88,
                    evidence="regarding the security of the ship Royal Charlotte",
                )
            ],
            temporal_date_hint="1792-10-24",
            spatial_location_hint="Canton",
        )

        mentions, relationships = kg_writer.write_extraction_result(
            db=self.db,
            block_id=self.test_block.id,
            result=sample_result,
        )
        self.db.commit()

        self.assertEqual(len(mentions), 2)
        self.assertEqual(len(relationships), 1)

        # Check PersonMention fields
        stored_mentions = self.db.query(PersonMention).filter_by(block_id=self.test_block.id).all()
        self.assertEqual(len(stored_mentions), 2)
        for m in stored_mentions:
            # A mention may now be linked to the provisional person the writer created
            # for the relationship endpoint — but only as a *candidate*, never as an
            # accepted identity, and never with a silently reviewed status.
            if m.person_id is not None:
                linked_person = self.db.query(Person).filter(Person.id == m.person_id).one()
                self.assertEqual(linked_person.identity_status, IdentityStatus.PROVISIONAL)
                self.assertEqual(m.resolution_status, ResolutionStatus.CANDIDATE)
                self.assertEqual(m.review_status, ReviewStatusKG.UNREVIEWED)
            else:
                self.assertEqual(m.resolution_status, ResolutionStatus.UNRESOLVED)
            self.assertEqual(m.temporal_date, date(1792, 10, 24))
            self.assertEqual(m.spatial_location, "Canton")
            self.assertEqual(m.block_id, self.test_block.id)

        # Check Relationship and Evidence linking
        stored_rel = self.db.query(Relationship).first()
        self.assertIsNotNone(stored_rel)
        self.assertEqual(stored_rel.relation_type, RelationType.GUARANTEE)

        # Evidence should connect to the block and mention
        evidences = self.db.query(RelationshipEvidence).filter_by(relationship_id=stored_rel.id).all()
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].block_id, self.test_block.id)
        self.assertIsNotNone(evidences[0].quote_text)

    def test_writer_deduplication_prevents_duplicates(self):
        """Repeatedly writing extraction on the same block does not produce duplicate mentions."""
        sample_result = KGExtractionResult(
            entities=[
                ExtractedEntity(mention="Puankhequa", confidence=0.9)
            ],
            relationships=[],
        )

        # First run
        m1, _ = kg_writer.write_extraction_result(
            db=self.db, block_id=self.test_block.id, result=sample_result
        )
        self.db.commit()
        self.assertEqual(len(m1), 1)

        # Second run (without overwrite)
        m2, _ = kg_writer.write_extraction_result(
            db=self.db, block_id=self.test_block.id, result=sample_result, overwrite_existing=False
        )
        self.db.commit()
        self.assertEqual(len(m2), 0)  # Reused existing mention, 0 newly created

        # Total count in DB remains 1
        count = self.db.query(PersonMention).filter_by(block_id=self.test_block.id).count()
        self.assertEqual(count, 1)

    def test_writer_overwrite_refreshes_mentions(self):
        """Writing with overwrite_existing=True safely resets and re-creates mentions."""
        sample_1 = KGExtractionResult(entities=[ExtractedEntity(mention="Mowqua", confidence=0.8)])
        kg_writer.write_extraction_result(db=self.db, block_id=self.test_block.id, result=sample_1)
        self.db.commit()

        # Overwrite with different mention
        sample_2 = KGExtractionResult(entities=[ExtractedEntity(mention="Chonqua", confidence=0.9)])
        kg_writer.write_extraction_result(db=self.db, block_id=self.test_block.id, result=sample_2, overwrite_existing=True)
        self.db.commit()

        mentions = self.db.query(PersonMention).filter_by(block_id=self.test_block.id).all()
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].raw_text, "Chonqua")

    # ----------------------------------------------------
    # 4. End-to-End Service Flow with Mocked LLM
    # ----------------------------------------------------

    def test_service_extract_from_block_mocked(self):
        """ExtractionService orchestrates text retrieval, LLM call, parsing and storage."""
        mock_llm_json = """{
          "entities": [
            {
              "mention": "Howqua",
              "normalized_name": "Howqua",
              "role": "Security Merchant",
              "confidence": 0.95,
              "evidence": "Howqua agreed to secure the vessel."
            },
            {
              "mention": "Captain Wilson",
              "normalized_name": "Wilson",
              "role": "Captain",
              "confidence": 0.90,
              "evidence": "Captain Wilson commanded the Royal Charlotte."
            }
          ],
          "relationships": [
            {
              "source_mention": "Howqua",
              "target_mention": "Captain Wilson",
              "relation_type": "commercial",
              "relation_label": "Port customs security",
              "confidence": 0.85,
              "evidence": "Howqua agreed to secure the vessel commanded by Captain Wilson."
            }
          ],
          "temporal_date_hint": "1792-11-05",
          "spatial_location_hint": "Whampoa"
        }"""

        async def run_test():
            with patch(
                "app.services.kg.llm_client.kg_llm_client.call_extraction_llm"
            ) as mock_call:
                mock_metrics = ExtractionTaskMetrics(
                    model_name="deepseek-ai/DeepSeek-V3",
                    latency_ms=125.0,
                    prompt_tokens=350,
                    completion_tokens=120,
                    total_tokens=470,
                )
                mock_call.return_value = (mock_llm_json, mock_metrics)

                response = await extraction_service.extract_from_block(
                    db=self.db,
                    block_id=self.test_block.id,
                )

                self.assertEqual(response.block_id, self.test_block.id)
                self.assertEqual(response.entities_extracted, 2)
                self.assertEqual(response.relationships_extracted, 1)
                self.assertEqual(response.metrics.total_tokens, 470)

                # Verify persistence in database
                mentions = self.db.query(PersonMention).filter_by(block_id=self.test_block.id).all()
                self.assertEqual(len(mentions), 2)
                rels = self.db.query(Relationship).all()
                self.assertEqual(len(rels), 1)

        asyncio.run(run_test())

    def test_service_extract_empty_block_skips(self):
        """ExtractionService gracefully handles blocks with empty text without calling LLM."""
        empty_block = Block(
            page_id=self.test_page.id,
            block_index=2,
            bbox=[0, 0, 100, 100],
            ocr_text_raw="   ",
            ocr_text_corrected="",
        )
        self.db.add(empty_block)
        self.db.commit()

        async def run_test():
            with patch("app.services.kg.llm_client.kg_llm_client.call_extraction_llm") as mock_call:
                response = await extraction_service.extract_from_block(
                    db=self.db,
                    block_id=empty_block.id,
                )
                mock_call.assert_not_called()
                self.assertEqual(response.entities_extracted, 0)
                self.assertEqual(response.relationships_extracted, 0)

        asyncio.run(run_test())

    # ----------------------------------------------------
    # 5. Acceptance Specific Tests: Error Recovery & Rollback
    # ----------------------------------------------------

    def test_failure_recovery_rolls_back_cleanly(self):
        """Simulate LLM network/timeout failure and verify DB leaves zero half-baked records."""
        async def run_test():
            from app.core import GPTServiceError
            with patch("app.services.kg.llm_client.kg_llm_client.call_extraction_llm") as mock_call:
                mock_call.side_effect = GPTServiceError("Simulated LLM Timeout Error")

                # Count before
                before_mentions = self.db.query(PersonMention).count()
                before_rels = self.db.query(Relationship).count()

                with self.assertRaises(GPTServiceError):
                    await extraction_service.extract_from_block(
                        db=self.db,
                        block_id=self.test_block.id,
                    )

                self.db.rollback()

                # Count after should remain identical
                after_mentions = self.db.query(PersonMention).count()
                after_rels = self.db.query(Relationship).count()
                self.assertEqual(before_mentions, after_mentions)
                self.assertEqual(before_rels, after_rels)

        asyncio.run(run_test())

    def test_invalid_json_recovery_rolls_back_cleanly(self):
        """Simulate LLM returning malformed non-JSON text and verify transactional safety."""
        async def run_test():
            with patch("app.services.kg.llm_client.kg_llm_client.call_extraction_llm") as mock_call:
                mock_metrics = ExtractionTaskMetrics(
                    model_name="deepseek-ai/DeepSeek-V3",
                    latency_ms=100.0,
                )
                mock_call.return_value = ("<<<INVALID GARBAGE NOT JSON>>>", mock_metrics)

                before_mentions = self.db.query(PersonMention).count()

                with self.assertRaises(RelationParserError):
                    await extraction_service.extract_from_block(
                        db=self.db,
                        block_id=self.test_block.id,
                    )

                self.db.rollback()

                after_mentions = self.db.query(PersonMention).count()
                self.assertEqual(before_mentions, after_mentions)

        asyncio.run(run_test())

    def test_negative_sample_zero_entities(self):
        """Negative sample (cargo weights / raw silk without persons) extracts zero entities safely."""
        negative_json = """{
          "entities": [],
          "relationships": [],
          "temporal_date_hint": "1792-10-20",
          "spatial_location_hint": "Canton"
        }"""

        async def run_test():
            with patch("app.services.kg.llm_client.kg_llm_client.call_extraction_llm") as mock_call:
                mock_metrics = ExtractionTaskMetrics(
                    model_name="deepseek-ai/DeepSeek-V3",
                    latency_ms=90.0,
                    prompt_tokens=200,
                    completion_tokens=25,
                    total_tokens=225,
                )
                mock_call.return_value = (negative_json, mock_metrics)

                resp = await extraction_service.extract_from_block(
                    db=self.db,
                    block_id=self.test_block.id,
                )
                self.assertEqual(resp.entities_extracted, 0)
                self.assertEqual(resp.relationships_extracted, 0)

                mentions = self.db.query(PersonMention).filter_by(block_id=self.test_block.id).all()
                self.assertEqual(len(mentions), 0)

        asyncio.run(run_test())

    def test_complex_multi_entity_relationships_and_directionality(self):
        """Validate multi-person extraction, directionality, and evidence grounding on archival interactions."""
        complex_json = """{
          "entities": [
            {
              "mention": "Mr. Pigou",
              "normalized_name": "Pigou",
              "role": "Supercargo",
              "organization": "East India Company",
              "mention_type": "person_name",
              "confidence": 0.95,
              "evidence": "Mr. Pigou by the consent of the Council, wrote immediately to Yuan, Hegu"
            },
            {
              "mention": "Yuan, Hegu",
              "normalized_name": "Yuan Hegu",
              "role": "Security Merchant",
              "organization": "Cohong",
              "mention_type": "alias",
              "confidence": 0.92,
              "evidence": "wrote immediately to Yuan, Hegu to request that he with the other Merchants would solicit the Mandarins"
            },
            {
              "mention": "Mandarins",
              "normalized_name": "Mandarins",
              "role": "Chinese Government Officials",
              "organization": "Qing Government",
              "mention_type": "title",
              "confidence": 0.88,
              "evidence": "solicit the Mandarins to use their best Endeavors"
            }
          ],
          "relationships": [
            {
              "source_mention": "Mr. Pigou",
              "target_mention": "Yuan, Hegu",
              "relation_type": "correspondence",
              "relation_label": "Official petition/letter",
              "directionality": "directed",
              "confidence": 0.90,
              "evidence": "Mr. Pigou by the consent of the Council, wrote immediately to Yuan, Hegu"
            },
            {
              "source_mention": "Yuan, Hegu",
              "target_mention": "Mandarins",
              "relation_type": "diplomatic",
              "relation_label": "Intercession/solicitation",
              "directionality": "directed",
              "confidence": 0.85,
              "evidence": "solicit the Mandarins to use their best Endeavors to find out the Offender"
            }
          ],
          "temporal_date_hint": "1792-06-17",
          "spatial_location_hint": "Canton"
        }"""

        async def run_test():
            with patch("app.services.kg.llm_client.kg_llm_client.call_extraction_llm") as mock_call:
                mock_metrics = ExtractionTaskMetrics(
                    model_name="deepseek-ai/DeepSeek-V3",
                    latency_ms=150.0,
                    prompt_tokens=450,
                    completion_tokens=220,
                    total_tokens=670,
                )
                mock_call.return_value = (complex_json, mock_metrics)

                resp = await extraction_service.extract_from_block(
                    db=self.db,
                    block_id=self.test_block.id,
                    overwrite_existing=True,
                )
                self.assertEqual(resp.entities_extracted, 3)
                self.assertEqual(resp.relationships_extracted, 2)

                # Verify database records
                mentions = self.db.query(PersonMention).filter_by(block_id=self.test_block.id).all()
                self.assertEqual(len(mentions), 3)
                m_names = {m.raw_text for m in mentions}
                self.assertIn("Mr. Pigou", m_names)
                self.assertIn("Yuan, Hegu", m_names)
                self.assertIn("Mandarins", m_names)

                # Verify relationship evidence linkage
                evidences = self.db.query(RelationshipEvidence).filter_by(block_id=self.test_block.id).all()
                self.assertEqual(len(evidences), 2)
                for ev in evidences:
                    self.assertIsNotNone(ev.quote_text)
                    self.assertEqual(ev.evidence_role, EvidenceRole.SUPPORTS)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
