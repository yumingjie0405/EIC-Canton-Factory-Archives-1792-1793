"""Boundary and edge-case acceptance tests for Stage 8 Phase 3.1 Evidence Contracts.

Focus areas:
1. Human review protection under CONFIRMED, REJECTED, CONTESTED, IN_REVIEW, UNREVIEWED
   (DEF-STAGE8-P3-01) and canonical verdict normalization (DEF-STAGE8-P3-02).
2. Conflict detection, non-conflation of neutral states (mentions_only, insufficient, unassessed), and preservation of all groundings.
3. Declarative vs runtime traceability (verified_block as Pydantic schema declaration vs DB existence).
4. Cross-document, cross-era, and cross-entity groundings in ClaimEvidenceSet.
5. Pydantic v2 JSON serialization roundtrip and default values.
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


class TestHumanReviewProtectionBoundaries(unittest.TestCase):
    """Area 1: In-depth boundary testing of human review protection."""

    def test_confirmed_human_verdict_strictly_protected(self):
        """When human_review_status == CONFIRMED, model re-run CANNOT overwrite human verdict or status."""
        grounding = EvidenceGrounding(
            claim_id="claim-b01",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=10,
            quote_text="Valid historical text citation from 1792.",
            human_review_status=ContractReviewStatus.CONFIRMED,
            human_verdict=HumanVerdict.SUPPORTED,
            reviewer="Historian A",
            review_notes="Verified against Canton Factory consultations.",
        )
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertTrue(grounding.is_historical_fact_proven())

        # Re-run model with contradiction
        grounding.apply_model_verification(
            verdict=ModelVerdict.REFUTED,
            entailment_score=0.05,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="nli-deberta-v3-audit",
        )

        # Automated fields updated
        self.assertEqual(grounding.model_verdict, "refuted")
        self.assertEqual(grounding.entailment_score, 0.05)
        # Human fields strictly intact
        self.assertEqual(grounding.human_review_status, "confirmed")
        self.assertEqual(grounding.human_verdict, "supported")
        self.assertEqual(grounding.reviewer, "Historian A")
        self.assertEqual(grounding.review_notes, "Verified against Canton Factory consultations.")
        # Effective verdict remains human verdict
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertTrue(grounding.is_historical_fact_proven())

    def test_rejected_human_review_protected(self):
        """DEF-STAGE8-P3-01: A scholar REJECTED finding survives an automated re-run."""
        grounding = EvidenceGrounding(
            claim_id="claim-b02",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=11,
            quote_text="Text rejected by scholar as fabricated or mistranslated.",
            evidence_relationship=EvidenceRelationship.CONTRADICTS,
            human_review_status=ContractReviewStatus.REJECTED,
            human_verdict=HumanVerdict.REFUTED,
            reviewer="Historian B",
            review_notes="Rejected: context proves this refers to a different transaction.",
        )

        grounding.apply_model_verification(
            verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.95,
            method=VerificationMethod.LLM_AS_A_JUDGE,
            model_version="gpt-judge-v1",
        )

        # Automated provenance is still recorded for the audit trail
        self.assertEqual(grounding.model_verdict, "supported")
        self.assertEqual(grounding.entailment_score, 0.95)
        self.assertEqual(grounding.model_version, "gpt-judge-v1")

        # Scholar record is untouched
        self.assertEqual(grounding.human_verdict, "refuted")
        self.assertEqual(grounding.human_review_status, "rejected")
        self.assertEqual(grounding.reviewer, "Historian B")
        self.assertEqual(
            grounding.review_notes,
            "Rejected: context proves this refers to a different transaction.",
        )

        # The rejection stands: the model cannot promote it to a supported relationship
        self.assertEqual(grounding.evidence_relationship, "contradicts")
        self.assertEqual(grounding.effective_verdict, "refuted")
        self.assertFalse(grounding.is_historical_fact_proven())

    def test_rejected_without_verdict_does_not_surrender_to_model(self):
        """REJECTED without an explicit verdict must not fall back to the model verdict."""
        grounding = EvidenceGrounding(
            claim_id="claim-b02b",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=12,
            quote_text="Evidence dismissed during review without a recorded verdict.",
            evidence_relationship=EvidenceRelationship.INSUFFICIENT,
            human_review_status=ContractReviewStatus.REJECTED,
            human_verdict=None,
        )

        grounding.apply_model_verification(
            verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.99,
            method=VerificationMethod.LLM_AS_A_JUDGE,
            model_version="gpt-judge-v1",
        )

        self.assertEqual(grounding.evidence_relationship, "insufficient")
        self.assertEqual(grounding.effective_verdict, "insufficient")
        self.assertNotEqual(grounding.effective_verdict, "supported")

    def test_contested_human_review_preserved(self):
        """DEF-STAGE8-P3-01: CONTESTED is preserved and never auto-cleared by a model re-run."""
        grounding = EvidenceGrounding(
            claim_id="claim-b03",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=12,
            quote_text="Contested historical record under academic debate.",
            human_review_status=ContractReviewStatus.CONTESTED,
            human_verdict=HumanVerdict.PARTIALLY_SUPPORTED,
            is_contested=True,
        )

        grounding.apply_model_verification(
            verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.88,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="nli-v1",
        )

        # Human audit fields remain intact
        self.assertEqual(grounding.human_verdict, "partially_supported")
        self.assertEqual(grounding.human_review_status, "contested")
        self.assertTrue(grounding.is_contested)
        self.assertTrue(grounding.is_scholarly_contested)
        # The model verdict is recorded but never displaces the scholar's finding
        self.assertEqual(grounding.model_verdict, "supported")
        self.assertEqual(grounding.effective_verdict, "partially_supported")
        # Relationship was NOT rewritten by the model
        self.assertEqual(grounding.evidence_relationship, "unassessed")

    def test_contested_flag_alone_is_not_cleared(self):
        """A contestation flag without status CONTESTED must survive a model re-run."""
        grounding = EvidenceGrounding(
            claim_id="claim-b03b",
            source_type=EvidenceSourceType.GRAPH_INFERENCE,
            provenance_status=ProvenanceStatus.UNRESOLVED,
            evidence_relationship=EvidenceRelationship.SUPPORTS,
            human_review_status=ContractReviewStatus.CONFIRMED,
            human_verdict=HumanVerdict.SUPPORTED,
            is_contested=True,
        )
        grounding.apply_model_verification(
            verdict=ModelVerdict.REFUTED,
            entailment_score=0.2,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="nli-v1",
        )
        self.assertTrue(grounding.is_contested)
        self.assertTrue(grounding.is_scholarly_contested)
        self.assertEqual(grounding.effective_verdict, "supported")

    def test_in_review_and_unreviewed_allow_model_update(self):
        """When status is UNREVIEWED or IN_REVIEW, model verification updates evidence_relationship."""
        grounding = EvidenceGrounding(
            claim_id="claim-b04",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=13,
            quote_text="Unreviewed archival evidence block text.",
            human_review_status=ContractReviewStatus.IN_REVIEW,
            evidence_relationship=EvidenceRelationship.UNASSESSED,
        )
        self.assertEqual(grounding.effective_verdict, "unassessed")

        grounding.apply_model_verification(
            verdict=ModelVerdict.REFUTED,
            entailment_score=0.10,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="nli-v1",
        )
        self.assertEqual(grounding.model_verdict, "refuted")
        self.assertEqual(grounding.evidence_relationship, "contradicts")
        self.assertEqual(grounding.effective_verdict, "refuted")
        # Model output alone never proves a historical fact
        self.assertFalse(grounding.is_historical_fact_proven())

    def test_unreviewed_allows_model_update(self):
        """UNREVIEWED carries no scholar authority, so automated evaluation stands."""
        grounding = EvidenceGrounding(
            claim_id="claim-b04b",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=14,
            quote_text="Unreviewed archival evidence block text for a second block.",
            human_review_status=ContractReviewStatus.UNREVIEWED,
        )
        grounding.apply_model_verification(
            verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.77,
            method=VerificationMethod.LEXICAL_OVERLAP,
            model_version="lexical-v1",
        )
        self.assertEqual(grounding.evidence_relationship, "supports")
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertEqual(grounding.human_review_status, "unreviewed")
        self.assertIsNone(grounding.human_verdict)

    def test_scholar_metadata_unchanged_across_all_statuses(self):
        """Model re-runs never touch verdict, reviewer, notes or review status for any state."""
        for status, verdict, expected_effective in (
            (ContractReviewStatus.CONFIRMED, HumanVerdict.SUPPORTED, "supported"),
            (ContractReviewStatus.REJECTED, HumanVerdict.REFUTED, "refuted"),
            (ContractReviewStatus.CONTESTED, HumanVerdict.PARTIALLY_SUPPORTED, "partially_supported"),
            (ContractReviewStatus.IN_REVIEW, None, "supported"),
            (ContractReviewStatus.UNREVIEWED, None, "supported"),
        ):
            with self.subTest(status=status):
                grounding = EvidenceGrounding(
                    claim_id=f"claim-meta-{status.value}",
                    source_type=EvidenceSourceType.PRIMARY_SOURCE,
                    provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
                    document_id=55,
                    block_id=21,
                    quote_text="A sufficiently long archival quotation for the metadata audit.",
                    human_review_status=status,
                    human_verdict=verdict,
                    reviewer="Historian C",
                    review_notes="Audit note preserved verbatim.",
                )
                grounding.apply_model_verification(
                    verdict=ModelVerdict.SUPPORTED,
                    entailment_score=0.9,
                    method=VerificationMethod.CROSS_ENCODER_NLI,
                    model_version="nli-v2",
                )

                self.assertEqual(grounding.human_review_status, status.value)
                self.assertEqual(grounding.human_verdict, verdict.value if verdict else None)
                self.assertEqual(grounding.reviewer, "Historian C")
                self.assertEqual(grounding.review_notes, "Audit note preserved verbatim.")
                self.assertEqual(grounding.effective_verdict, expected_effective)
                if status in (
                    ContractReviewStatus.UNREVIEWED,
                    ContractReviewStatus.IN_REVIEW,
                ):
                    self.assertEqual(grounding.model_verdict, "supported")

    def test_model_output_none_falls_back_to_evidence_relationship(self):
        """If model_verdict is None, effective_verdict returns evidence_relationship, never defaults to supported."""
        grounding = EvidenceGrounding(
            claim_id="claim-b05",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=14,
            quote_text="Some text without model evaluation.",
            evidence_relationship=EvidenceRelationship.UNASSESSED,
            model_verdict=None,
            human_verdict=None,
        )
        self.assertEqual(grounding.effective_verdict, "unassessed")
        self.assertNotEqual(grounding.effective_verdict, "supported")


class TestConflictAggregationBoundaries(unittest.TestCase):
    """Area 2: Boundary testing of conflicting evidence aggregation."""

    def test_conflicting_evidence_preserves_all_groundings(self):
        """Multiple conflicting evidences for a claim are all preserved without silent pruning."""
        claim = ResearchClaim(
            claim_id="claim-multi-01",
            claim_text="1792年两广总督福康安准许东印度公司延缓结清洋税。",
            target_year=1792,
        )

        g1 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=101,
            quote_text="The Viceroy Fuk'anggan granted permission to defer payment.",
            evidence_relationship=EvidenceRelationship.SUPPORTS,
            model_verdict=ModelVerdict.SUPPORTED,
        )
        g2 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=54,
            block_id=202,
            quote_text="Strict orders were issued that no duties could be deferred under any circumstance.",
            evidence_relationship=EvidenceRelationship.CONTRADICTS,
            model_verdict=ModelVerdict.REFUTED,
        )

        claim_set = ClaimEvidenceSet(claim=claim, groundings=[g1, g2])

        # Both groundings remain intact in collection
        self.assertEqual(len(claim_set.groundings), 2)
        self.assertEqual(claim_set.groundings[0].block_id, 101)
        self.assertEqual(claim_set.groundings[1].block_id, 202)
        self.assertTrue(claim_set.has_conflicts)
        self.assertEqual(claim_set.aggregated_status, "contested")

    def test_neutral_states_not_treated_as_conflict_or_support(self):
        """mentions_only, insufficient, unassessed must not trigger has_conflicts or return supported."""
        claim = ResearchClaim(claim_id="c-neutral", claim_text="Neutral test claim.")

        g_mentions = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=10,
            quote_text="Only mentions the company trade without asserting the tariff.",
            evidence_relationship=EvidenceRelationship.MENTIONS_ONLY,
        )
        g_insufficient = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=11,
            quote_text="A fragmented sentence lacking any financial terms.",
            evidence_relationship=EvidenceRelationship.INSUFFICIENT,
        )

        c_set = ClaimEvidenceSet(claim=claim, groundings=[g_mentions, g_insufficient])
        # Neither supports nor refutes, so has_conflicts MUST be False
        self.assertFalse(c_set.has_conflicts)
        # Aggregated status must not be supported or contested
        self.assertNotEqual(c_set.aggregated_status, "supported")
        self.assertNotEqual(c_set.aggregated_status, "contested")
        self.assertEqual(c_set.aggregated_status, "inconclusive")

    def test_cross_era_and_cross_entity_grounding_limitation(self):
        """INVESTIGATION: EvidenceGrounding lacks temporal_year and entity_ids fields.
        Demonstrates that ClaimEvidenceSet relies purely on caller to align context.
        """
        claim_1792 = ResearchClaim(
            claim_id="claim-1792",
            claim_text="1792年债务纠纷情况。",
            target_year=1792,
            entity_names=["Henry Browne"],
        )

        # Grounding from 1779 Document 4 mistakenly attached to 1792 claim
        g_1779 = EvidenceGrounding(
            claim_id=claim_1792.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=4,  # Doc 4 is from 1779!
            block_id=34,
            quote_text="Debts of Co-Hong merchants settled in 1779.",
            evidence_relationship=EvidenceRelationship.SUPPORTS,
        )

        claim_set = ClaimEvidenceSet(claim=claim_1792)
        # Adding succeeds because grounding.claim_id matches
        claim_set.add_grounding(g_1779)
        self.assertEqual(len(claim_set.groundings), 1)

        # ARCHITECTURAL LIMITATION CONFIRMATION:
        # EvidenceGrounding does not have a target_year field, so ClaimEvidenceSet
        # cannot detect that g_1779 is from 1779 unless caller or database does.
        self.assertFalse(hasattr(g_1779, "target_year"))
        self.assertFalse(hasattr(g_1779, "entity_names"))

    def test_conflict_detected_after_model_rerun(self):
        """DEF-STAGE8-P3-02 regression: model-updated verdicts still aggregate correctly."""
        claim = ResearchClaim(claim_id="claim-enum-bug", claim_text="Enum string mismatch test.")
        g1 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=1,
            quote_text="Some valid quotation 1",
        )
        g2 = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=2,
            quote_text="Some valid quotation 2",
        )

        g1.apply_model_verification(
            verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.9,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="v1",
        )
        g2.apply_model_verification(
            verdict=ModelVerdict.REFUTED,
            entailment_score=0.1,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="v1",
        )

        # Canonical lowercase values, never 'ModelVerdict.SUPPORTED'
        self.assertEqual(g1.effective_verdict, "supported")
        self.assertEqual(g2.effective_verdict, "refuted")

        c_set = ClaimEvidenceSet(claim=claim, groundings=[g1, g2])
        self.assertTrue(c_set.has_conflicts)
        self.assertEqual(c_set.aggregated_status, "contested")

    def test_partial_support_and_refutation_conflict(self):
        """Partial support opposite a refutation is still a conflict, not a supported claim."""
        claim = ResearchClaim(claim_id="claim-partial-01", claim_text="Partial conflict claim.")
        g_partial = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=31,
            quote_text="The record supports the date but not the amount.",
            evidence_relationship=EvidenceRelationship.PARTIALLY_SUPPORTS,
        )
        g_refute = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=32,
            quote_text="A separate entry contradicts the amount entirely.",
            evidence_relationship=EvidenceRelationship.CONTRADICTS,
        )
        c_set = ClaimEvidenceSet(claim=claim, groundings=[g_partial, g_refute])
        self.assertTrue(c_set.has_conflicts)
        self.assertEqual(c_set.aggregated_status, "contested")

    def test_aggregated_status_matches_evidence_combination(self):
        """aggregated_status must reflect the actual combination of evidence verdicts."""
        claim = ResearchClaim(claim_id="claim-agg-01", claim_text="Aggregation consistency claim.")

        def make(block_id: int, relationship) -> EvidenceGrounding:
            return EvidenceGrounding(
                claim_id=claim.claim_id,
                source_type=EvidenceSourceType.PRIMARY_SOURCE,
                provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
                document_id=55,
                block_id=block_id,
                quote_text=f"Quotation body for block {block_id}.",
                evidence_relationship=relationship,
            )

        scenarios = (
            ("no_evidence", []),
            ("supported", [make(41, EvidenceRelationship.SUPPORTS)]),
            ("refuted", [make(42, EvidenceRelationship.CONTRADICTS)]),
            ("unassessed", [make(43, EvidenceRelationship.UNASSESSED)]),
            ("mentions_only", [make(44, EvidenceRelationship.MENTIONS_ONLY)]),
            ("partially_supported", [make(45, EvidenceRelationship.PARTIALLY_SUPPORTS)]),
            (
                "inconclusive",
                [
                    make(46, EvidenceRelationship.MENTIONS_ONLY),
                    make(47, EvidenceRelationship.INSUFFICIENT),
                ],
            ),
            (
                "contested",
                [
                    make(48, EvidenceRelationship.SUPPORTS),
                    make(49, EvidenceRelationship.CONTRADICTS),
                ],
            ),
        )
        for expected, groundings in scenarios:
            with self.subTest(expected=expected):
                c_set = ClaimEvidenceSet(claim=claim, groundings=groundings)
                self.assertEqual(c_set.aggregated_status, expected)

    def test_contested_status_keeps_claim_set_contested(self):
        """A single CONTESTED grounding keeps the aggregate contested even when model says supported."""
        claim = ResearchClaim(claim_id="claim-agg-02", claim_text="Dispute preservation claim.")
        disputed = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=51,
            quote_text="Disputed reading of the consultation entry.",
            human_review_status=ContractReviewStatus.CONTESTED,
            human_verdict=HumanVerdict.PARTIALLY_SUPPORTED,
        )
        disputed.apply_model_verification(
            verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.91,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="nli-v1",
        )
        c_set = ClaimEvidenceSet(claim=claim, groundings=[disputed])
        self.assertTrue(c_set.has_contested_groundings)
        self.assertEqual(c_set.aggregated_status, "contested")
        self.assertNotEqual(c_set.aggregated_status, "supported")


class TestVerdictNormalization(unittest.TestCase):
    """Area 2b: DEF-STAGE8-P3-02 canonical value handling for all accepted input shapes."""

    def _grounding(self, **kwargs) -> EvidenceGrounding:
        payload = dict(
            claim_id="claim-norm",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=61,
            quote_text="Normalization probe quotation text.",
        )
        payload.update(kwargs)
        return EvidenceGrounding(**payload)

    def test_enum_instance_is_normalized(self):
        grounding = self._grounding()
        grounding.apply_model_verification(
            verdict=ModelVerdict.PARTIALLY_SUPPORTED,
            entailment_score=0.5,
            method=VerificationMethod.CROSS_ENCODER_NLI,
            model_version="v1",
        )
        self.assertEqual(grounding.model_verdict, "partially_supported")
        self.assertEqual(grounding.effective_verdict, "partially_supported")
        self.assertEqual(grounding.evidence_relationship, "partially_supports")
        self.assertEqual(grounding.verification_method, "cross_encoder_nli")

    def test_standard_string_verdict_is_accepted(self):
        grounding = self._grounding()
        grounding.apply_model_verification(
            verdict="SUPPORTED",
            entailment_score=0.8,
            method="llm_as_a_judge",
            model_version="v1",
        )
        self.assertEqual(grounding.model_verdict, "supported")
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertEqual(grounding.evidence_relationship, "supports")
        self.assertEqual(grounding.verification_method, "llm_as_a_judge")

    def test_legacy_enum_repr_string_is_normalized(self):
        """Values persisted by the buggy version ('ModelVerdict.SUPPORTED') are repaired on read."""
        grounding = self._grounding()
        grounding.model_verdict = "ModelVerdict.SUPPORTED"
        self.assertEqual(grounding.effective_verdict, "supported")
        self.assertNotIn("ModelVerdict", grounding.effective_verdict)

    def test_legacy_enum_repr_relationship_is_normalized(self):
        grounding = self._grounding()
        grounding.evidence_relationship = "EvidenceRelationship.SUPPORTS"
        self.assertEqual(grounding.effective_verdict, "supports")

    def test_unrecognized_model_verdict_is_rejected(self):
        grounding = self._grounding()
        with self.assertRaises(ValueError):
            grounding.apply_model_verification(
                verdict="definitely_supports_everything",
                entailment_score=0.9,
                method=VerificationMethod.LLM_AS_A_JUDGE,
                model_version="rogue-v1",
            )
        # Nothing was mutated by the rejected call
        self.assertIsNone(grounding.model_verdict)
        self.assertEqual(grounding.evidence_relationship, "unassessed")

    def test_non_string_model_payload_never_becomes_support(self):
        grounding = self._grounding()
        grounding.model_verdict = 1
        self.assertEqual(grounding.effective_verdict, "unassessed")
        self.assertNotEqual(grounding.effective_verdict, "supported")

    def test_none_model_verdict_falls_back_to_relationship(self):
        grounding = self._grounding(evidence_relationship=EvidenceRelationship.MENTIONS_ONLY)
        self.assertEqual(grounding.effective_verdict, "mentions_only")

    def test_unrecognized_review_status_never_promotes_model_output(self):
        """A corrupt review status must not let automated output become the effective verdict."""
        grounding = self._grounding(evidence_relationship=EvidenceRelationship.UNASSESSED)
        grounding.human_review_status = "definitely_reviewed"
        grounding.model_verdict = "supported"
        self.assertEqual(grounding.effective_verdict, "unassessed")


class TestEvidenceTraceabilityAndDeclarativeBoundaries(unittest.TestCase):
    """Area 3: Boundary testing of evidence traceability and declarative vs verified status."""

    def test_declarative_nature_of_verified_block(self):
        """CRITICAL: verified_block is a declarative state in Pydantic schema, NOT a live DB existence check."""
        # A block_id that does not exist in any database can still instantiate EvidenceGrounding
        # as long as Pydantic structural validation rules (integers, quote >= 5 chars) pass.
        grounding = EvidenceGrounding(
            claim_id="claim-trace-01",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=999999,  # Fictitious doc_id
            block_id=888888,     # Fictitious block_id
            quote_text="Historical quote declared as verified block.",
        )
        self.assertEqual(grounding.provenance_status, "verified_block")
        # is_traceable_primary_evidence evaluates the structural attributes
        self.assertTrue(grounding.is_traceable_primary_evidence)
        # Note: This proves that schema-level traceability is STRUCTURAL, not RUNTIME DB PROVEN.

    def test_missing_block_status_disables_traceability(self):
        """When block_id is null or provenance_status is MISSING_BLOCK, is_traceable is False."""
        grounding = EvidenceGrounding(
            claim_id="claim-trace-02",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.MISSING_BLOCK,
            document_id=55,
            block_id=None,
            quote_text="Quote from a missing block.",
        )
        self.assertFalse(grounding.is_traceable_primary_evidence)

    def test_quote_text_length_threshold_boundary(self):
        """Verified block requires quote_text >= 5 non-whitespace characters."""
        # Exactly 4 characters -> raises ValidationError
        with self.assertRaises(ValidationError):
            EvidenceGrounding(
                claim_id="claim-trace-03",
                source_type=EvidenceSourceType.PRIMARY_SOURCE,
                provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
                document_id=55,
                block_id=1,
                quote_text="four",
            )

        # Exactly 5 characters -> valid
        grounding = EvidenceGrounding(
            claim_id="claim-trace-03",
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            block_id=1,
            quote_text="five!",
        )
        self.assertEqual(grounding.quote_text, "five!")

    def test_external_secondary_source_representation(self):
        """External secondary sources can use document_name and EXTERNAL_RECORD status."""
        grounding = EvidenceGrounding(
            claim_id="claim-trace-04",
            source_type=EvidenceSourceType.EXTERNAL_SECONDARY_SOURCE,
            provenance_status=ProvenanceStatus.EXTERNAL_RECORD,
            document_name="梁嘉彬《广东十三行考》第142页",
            quote_text="梁嘉彬考证：天宝行梁经国于乾隆五十七年（1792）承充行商。",
            text_provenance=TextProvenance.HUMAN_VERIFIED,
            evidence_relationship=EvidenceRelationship.SUPPORTS,
        )
        self.assertEqual(grounding.source_type, "external_secondary_source")
        self.assertEqual(grounding.provenance_status, "external_record")
        # External source is NOT a primary archival block citation
        self.assertFalse(grounding.is_traceable_primary_evidence)


class TestDataContractCompatibilityAndSerialization(unittest.TestCase):
    """Area 4: Serialization, defaults, and cross-module schema compatibility."""

    def test_json_roundtrip_serialization(self):
        """Ensure full JSON serialization and deserialization retains all fields."""
        claim = ResearchClaim(
            claim_text="1792年广州商馆大班与两广总督会面交涉。",
            claim_category=ClaimCategory.FACTUAL,
            target_year=1792,
            entity_names=["Henry Browne", "Fuk'anggan"],
        )
        grounding = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.PRIMARY_SOURCE,
            provenance_status=ProvenanceStatus.VERIFIED_BLOCK,
            document_id=55,
            page_number=14,
            block_id=102,
            quote_text="The President met with the Viceroy at the government palace.",
            text_provenance=TextProvenance.HUMAN_VERIFIED,
            evidence_relationship=EvidenceRelationship.SUPPORTS,
            model_verdict=ModelVerdict.SUPPORTED,
            entailment_score=0.92,
            human_review_status=ContractReviewStatus.CONFIRMED,
            human_verdict=HumanVerdict.SUPPORTED,
            reviewer="Scholar C",
        )
        claim_set = ClaimEvidenceSet(claim=claim, groundings=[grounding])

        # Serialize to JSON dict
        dumped = claim_set.model_dump()
        self.assertEqual(dumped["claim"]["target_year"], 1792)
        self.assertEqual(dumped["groundings"][0]["block_id"], 102)
        self.assertEqual(dumped["groundings"][0]["human_review_status"], "confirmed")

        # Deserialize back
        reloaded = ClaimEvidenceSet(**dumped)
        self.assertEqual(reloaded.claim.claim_id, claim.claim_id)
        self.assertEqual(len(reloaded.groundings), 1)
        self.assertEqual(reloaded.groundings[0].effective_verdict, "supported")

    def test_default_values_contract(self):
        """Verify explicit default values for unprovided optional fields."""
        claim = ResearchClaim(claim_text="Default value assertion test.")
        self.assertEqual(claim.claim_category, "factual")
        self.assertEqual(claim.review_status, "unreviewed")
        self.assertEqual(claim.created_by, "agent")
        self.assertIsNone(claim.target_year)
        self.assertEqual(claim.entity_ids, [])
        self.assertEqual(claim.entity_names, [])

        grounding = EvidenceGrounding(
            claim_id=claim.claim_id,
            source_type=EvidenceSourceType.UNKNOWN,
        )
        self.assertEqual(grounding.provenance_status, "unresolved")
        self.assertEqual(grounding.text_provenance, "unknown")
        self.assertEqual(grounding.evidence_relationship, "unassessed")
        self.assertEqual(grounding.verification_method, "unverified")
        self.assertEqual(grounding.human_review_status, "unreviewed")
        self.assertFalse(grounding.is_contested)


if __name__ == "__main__":
    unittest.main()
