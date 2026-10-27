"""Unit tests for Stage 8 Phase 3.1: Evidence Classification and Data Contracts.

Validates:
1. All legal enum values for the three independent concepts.
2. Distinct semantics: raw_ocr vs corrected_ocr vs human_transcription vs human_verified.
3. Distinction among supports, contradicts, partially_supports, mentions_only, insufficient, unassessed.
4. Unassessed evidence cannot default to supports or insufficient.
5. Mentions_only must NEVER be treated as supports.
6. Coexistence of multiple conflicting evidences for a single research claim.
7. Model verdict vs human verdict independence (Model re-run does not overwrite confirmed human review).
8. Model confidence score cannot directly convert to historical fact truth.
9. Traceability constraints: any missing block/doc/quote cannot be marked as traceable primary evidence.
10. Rejection of illegal enums and invalid data combinations by Pydantic validators.
"""
import unittest
from datetime import datetime
from pydantic import ValidationError

from app.schemas.evidence_contract import (
    ClaimCategory,
    ClaimEvidenceSet,
    ContractReviewStatus,
    EvidenceGrounding,
    EvidenceRelationship,
    EvidenceSourceType,
    HumanVerdict,
    ModelVerdict,
    ProvenanceStatus,
    ResearchClaim,
    TextProvenance,
    VerificationMethod,
)


class TestEvidenceEnums(unittest.TestCase):
    """Test 1: Verify all legal controlled vocabulary values and illegal value rejections."""

    def test_evidence_source_type_values(self):
        expected = {"primary_source", "graph_inference", "external_secondary_source", "unknown"}
        actual = {e.value for e in EvidenceSourceType}
        self.assertEqual(expected, actual)
        self.assertEqual(EvidenceSourceType.PRIMARY_SOURCE.value, "primary_source")
        self.assertEqual(EvidenceSourceType.GRAPH_INFERENCE.value, "graph_inference")
        self.assertEqual(EvidenceSourceType.EXTERNAL_SECONDARY_SOURCE.value, "external_secondary_source")
        self.assertEqual(EvidenceSourceType.UNKNOWN.value, "unknown")

    def test_text_provenance_values(self):
        expected = {"raw_ocr", "corrected_ocr", "human_transcription", "human_verified", "unknown"}
        actual = {e.value for e in TextProvenance}
        self.assertEqual(expected, actual)
        # Verify strict distinction between human transcription (from manuscript) and human verified (proofread)
        self.assertNotEqual(TextProvenance.HUMAN_TRANSCRIPTION, TextProvenance.HUMAN_VERIFIED)

    def test_evidence_relationship_values(self):
        expected = {"supports", "contradicts", "partially_supports", "mentions_only", "insufficient", "unassessed"}
        actual = {e.value for e in EvidenceRelationship}
        self.assertEqual(expected, actual)

    def test_verdict_and_review_enums(self):
        self.assertIn("supported", {v.value for v in ModelVerdict})
        self.assertIn("unverifiable", {v.value for v in ModelVerdict})
        self.assertIn("confirmed", {v.value for v in ContractReviewStatus})
        self.assertIn("contested", {v.value for v in ContractReviewStatus})
        self.assertIn("verified_block", {v.value for v in ProvenanceStatus})
        self.assertIn("missing_block", {v.value for v in ProvenanceStatus})


class TestResearchClaimContract(unittest.TestCase):
    """Test 2: Verify ResearchClaim creation, fields, and constraints."""

    def test_valid_research_claim_creation(self):
        claim = ResearchClaim(
            claim_text="1792年东印度公司大班亨利·布朗与行商浩官商定毛织品合同价格。",
            claim_category=ClaimCategory.FACTUAL,
            target_year=1792,
            entity_names=["Henry Browne", "Howqua"],
            created_by="agent",
            review_status=ContractReviewStatus.UNREVIEWED,
        )
        self.assertIsNotNone(claim.claim_id)
        self.assertEqual(claim.target_year, 1792)
        self.assertEqual(claim.claim_category, "factual")
        self.assertEqual(claim.review_status, "unreviewed")
        self.assertEqual(len(claim.entity_names), 2)

    def test_reject_empty_claim_text(self):
        with self.assertRaises(ValidationError):
            ResearchClaim(claim_text="   ")

    def test_reject_invalid_year_out_of_bounds(self):
        with self.assertRaises(ValidationError):
            ResearchClaim(
                claim_text="测试超界年份",
                target_year=999,  # ge=1500
            )


class TestTextProvenanceDistinctions(unittest.TestCase):
    """Test 3: Semantic distinctions among raw OCR, corrected OCR, human transcription, human verified."""

    def test_text_provenance_distinct_layers(self):
        # 1. Raw OCR: vision model direct text with possible archaic noise
        grounding_raw = EvidenceGrounding(
            claim_id="claim-001",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=120,
            quote_text="At a Conſultation Present Henry Browne Esqr...",
            text_provenance=TextProvenance.RAW_OCR,
        )
        self.assertEqual(grounding_raw.text_provenance, "raw_ocr")

        # 2. Corrected OCR: LLM post-processed
        grounding_corr = EvidenceGrounding(
            claim_id="claim-001",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=120,
            quote_text="At a Consultation Present Henry Browne Esqr...",
            text_provenance=TextProvenance.CORRECTED_OCR,
        )
        self.assertEqual(grounding_corr.text_provenance, "corrected_ocr")

        # 3. Human Transcription: manually typed from manuscript image
        grounding_trans = EvidenceGrounding(
            claim_id="claim-001",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=120,
            quote_text="At a Consultation Present Henry Browne Esqr...",
            text_provenance=TextProvenance.HUMAN_TRANSCRIPTION,
        )
        self.assertEqual(grounding_trans.text_provenance, "human_transcription")

        # 4. Human Verified: proofread and verified against manuscript by scholar
        grounding_ver = EvidenceGrounding(
            claim_id="claim-001",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=120,
            quote_text="At a Consultation Present Henry Browne Esqr...",
            text_provenance=TextProvenance.HUMAN_VERIFIED,
        )
        self.assertEqual(grounding_ver.text_provenance, "human_verified")

        # Assert all 4 are distinct
        self.assertNotEqual(grounding_raw.text_provenance, grounding_corr.text_provenance)
        self.assertNotEqual(grounding_corr.text_provenance, grounding_trans.text_provenance)
        self.assertNotEqual(grounding_trans.text_provenance, grounding_ver.text_provenance)


class TestEvidenceRelationshipsAndInvariants(unittest.TestCase):
    """Test 4: Invariants governing semantic relationships, mentions_only, unassessed."""

    def test_unassessed_never_defaults_to_supports_or_insufficient(self):
        grounding = EvidenceGrounding(
            claim_id="claim-002",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=120,
            quote_text="The committee met at the British Factory in Canton.",
            evidence_relationship=EvidenceRelationship.UNASSESSED,
            model_verdict=None,
            human_verdict=None,
        )
        self.assertEqual(grounding.evidence_relationship, "unassessed")
        # effective_verdict must return 'unassessed', never 'supports' or 'insufficient'
        self.assertEqual(grounding.effective_verdict, "unassessed")
        self.assertFalse(grounding.is_historical_fact_proven())

    def test_mentions_only_never_treated_as_supports(self):
        grounding = EvidenceGrounding(
            claim_id="claim-003",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=121,
            quote_text="Mentioned that woollens were landed at the warehouse.",
            evidence_relationship=EvidenceRelationship.MENTIONS_ONLY,
        )
        self.assertEqual(grounding.evidence_relationship, "mentions_only")
        self.assertEqual(grounding.effective_verdict, "mentions_only")
        self.assertNotEqual(grounding.effective_verdict, "supports")
        self.assertNotEqual(grounding.effective_verdict, "supported")

    def test_all_relationship_types_distinct(self):
        types = [
            EvidenceRelationship.SUPPORTS,
            EvidenceRelationship.CONTRADICTS,
            EvidenceRelationship.PARTIALLY_SUPPORTS,
            EvidenceRelationship.MENTIONS_ONLY,
            EvidenceRelationship.INSUFFICIENT,
            EvidenceRelationship.UNASSESSED,
        ]
        self.assertEqual(len(types), 6)
        self.assertEqual(len(set(t.value for t in types)), 6)


class TestAuditIsolationAndPrecedence(unittest.TestCase):
    """Test 5: Model verdict vs human audit verdict isolation and precedence."""

    def test_model_rerun_does_not_overwrite_confirmed_human_verdict(self):
        # Scholar reviewed and confirmed as supported
        grounding = EvidenceGrounding(
            claim_id="claim-004",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=125,
            quote_text="Howqua attended and settled the woolen contracts for 1792.",
            human_review_status=ContractReviewStatus.CONFIRMED,
            human_verdict=HumanVerdict.SUPPORTED,
            reviewer="Dr. Liang (Scholar)",
            review_notes="Verified against original Canton consultations diary.",
        )
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertTrue(grounding.is_historical_fact_proven())

        # An automated model re-run mistakenly classifies it as REFUTED or UNVERIFIABLE
        grounding.apply_model_verification(
            verdict=ModelVerdict.REFUTED,
            entailment_score=0.12,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="nli-model-v2",
        )

        # Automated fields are updated
        self.assertEqual(grounding.model_verdict, "refuted")
        self.assertEqual(grounding.entailment_score, 0.12)
        self.assertEqual(grounding.model_version, "nli-model-v2")

        # CRITICAL INVARIANT: Human verdict and confirmed status CANNOT be overwritten!
        self.assertEqual(grounding.human_verdict, "supported")
        self.assertEqual(grounding.human_review_status, "confirmed")
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertTrue(grounding.is_historical_fact_proven())

    def test_model_confidence_cannot_directly_prove_historical_truth(self):
        # Automated model reports 0.999 confidence, but unreviewed by human
        grounding = EvidenceGrounding(
            claim_id="claim-005",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=126,
            quote_text="The Select Committee received the report from Macao.",
            model_verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.999,
            human_review_status=ContractReviewStatus.UNREVIEWED,
            human_verdict=None,
        )
        # Effective verdict reports model_verdict for display
        self.assertEqual(grounding.effective_verdict, "supported")
        # But is_historical_fact_proven MUST return False!
        self.assertFalse(grounding.is_historical_fact_proven())


class TestTraceabilityAndMissingSourceConstraints(unittest.TestCase):
    """Test 6: Archival source grounding validation, missing block status, and traceability."""

    def test_is_traceable_primary_evidence_success(self):
        grounding = EvidenceGrounding(
            claim_id="claim-006",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=127,
            page_number=12,
            quote_text="Valid historical quotation from Canton Factory consultations.",
            text_provenance=TextProvenance.HUMAN_VERIFIED,
        )
        self.assertTrue(grounding.is_traceable_primary_evidence)

    def test_missing_block_cannot_be_traceable_primary_evidence(self):
        grounding = EvidenceGrounding(
            claim_id="claim-006",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.MISSING_BLOCK,
            document_id=55,
            block_id=None,
            quote_text="Valid text but block was purged or not found.",
        )
        self.assertFalse(grounding.is_traceable_primary_evidence)

    def test_graph_inference_cannot_be_traceable_primary_evidence(self):
        grounding = EvidenceGrounding(
            claim_id="claim-006",
            source_type=EvidenceSourceType.GRAPH_INFERENCE,
            provenance_status=ProvenanceStatus.EXTERNAL_RECORD,
            quote_text="Inferred commercial link between Browne and Howqua.",
            text_provenance=TextProvenance.CORRECTED_OCR,
        )
        self.assertFalse(grounding.is_traceable_primary_evidence)

    def test_reject_verified_block_with_null_block_id(self):
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="claim-007",
                source_type=EvidenceSourceType.PRIMARY_SOURCE,
                provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
                document_id=55,
                block_id=None,  # ILLEGAL: verified_block requires block_id
                quote_text="Some text here that has length",
            )

    def test_reject_verified_block_with_empty_quote(self):
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="claim-007",
                source_type=EvidenceSourceType.PRIMARY_SOURCE,
                provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
                document_id=55,
                block_id=10,
                quote_text="",  # ILLEGAL: verified_block requires non-empty quote >= 5 chars
            )

    def test_reject_graph_inference_masquerading_as_raw_ocr(self):
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="claim-008",
                source_type=EvidenceSourceType.GRAPH_INFERENCE,
                provenance_status=ProvenanceStatus.UNRESOLVED,
                quote_text="Graph inference statement pretending to be OCR",
                text_provenance=TextProvenance.RAW_OCR,  # ILLEGAL: graph inference cannot claim raw OCR
            )


class TestConflictingEvidenceCoexistence(unittest.TestCase):
    """Test 7: Multiple conflicting evidences for a single claim, ensuring non-silent overwrite."""

    def test_conflicting_evidence_set(self):
        claim = ResearchClaim(
            claim_id="claim-conflict-01",
            claim_text="1792年公行行商同意调降茶税规费10%。",
            claim_category=ClaimCategory.FACTUAL,
            target_year=1792,
        )

        # Evidence 1: Doc 55 (1792) records support
        e1 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=201,
            quote_text="The Hong merchants agreed to the reduction of duties.",
            evidence_relationship=EvidenceRelationship.SUPPORTS,
            model_verdict=ModelVerdict.SUPPORTED,
        )

        # Evidence 2: Doc 4 (1779) records contrary financial stance
        e2 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=4,
            block_id=34,
            quote_text="The merchants protested vigorously against any reduction in fees.",
            evidence_relationship=EvidenceRelationship.CONTRADICTS,
            model_verdict=ModelVerdict.REFUTED,
            is_contested=True,
        )

        claim_set = ClaimEvidenceSet(claim=claim)
        claim_set.add_grounding(e1)
        claim_set.add_grounding(e2)

        # Assert both evidences coexist in the same claim
        self.assertEqual(len(claim_set.groundings), 2)
        self.assertTrue(claim_set.has_conflicts)
        # Conflicting evidences must NOT be silently collapsed to supported!
        self.assertEqual(claim_set.aggregated_status, "contested")
        self.assertNotEqual(claim_set.aggregated_status, "supported")

    def test_mentions_only_claim_set_aggregated_status(self):
        claim = ResearchClaim(
            claim_id="claim-mentions-01",
            claim_text="1792年发生大火烧毁十三行商馆。",
        )
        e1 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=301,
            quote_text="Mention of minor fire in the suburbs, no damage to factory.",
            evidence_relationship=EvidenceRelationship.MENTIONS_ONLY,
        )
        claim_set = ClaimEvidenceSet(claim=claim, groundings=[e1])
        self.assertFalse(claim_set.has_conflicts)
        self.assertEqual(claim_set.aggregated_status, "mentions_only")
        self.assertNotEqual(claim_set.aggregated_status, "supported")


class TestIllegalInputsAndValidationRejections(unittest.TestCase):
    """Test 8: Ensure invalid enum strings and data types are rejected."""

    def test_reject_illegal_evidence_source_type(self):
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="c1",
                source_type="wikipedia_article",  # Illegal enum
            )

    def test_reject_illegal_relationship_type(self):
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="c1",
                source_type=EvidenceSourceType.PRIMARY_SOURCE,
                evidence_relationship="definitely_true",  # Illegal enum
            )

    def test_reject_entailment_score_out_of_bounds(self):
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="c1",
                source_type=EvidenceSourceType.PRIMARY_SOURCE,
                entailment_score=1.5,  # Out of [0.0, 1.0]
            )

    def test_claim_evidence_set_rejects_mismatched_claim_id(self):
        claim = ResearchClaim(claim_id="claim-AAA", claim_text="Test claim AAA")
        grounding = EvidenceGrounding(
            claim_id="claim-BBB",  # Mismatch!
            source_type=EvidenceSourceType.UNKNOWN,
        )
        claim_set = ClaimEvidenceSet(claim=claim)
        with self.assertRaises(ValueError):
            claim_set.add_grounding(grounding)


if __name__ == "__main__":
    unittest.main()
