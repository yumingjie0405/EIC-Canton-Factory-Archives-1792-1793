"""Unit tests for Knowledge Graph Pydantic schemas."""
from datetime import date
import unittest

from pydantic import ValidationError

from app.models.kg.enums import (
    AuthoritySource,
    ClaimStatus,
    Directionality,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.schemas.kg import (
    ExternalAuthorityCreate,
    ExternalAuthorityUpdate,
    PersonCreate,
    PersonMentionCreate,
    PersonMentionUpdate,
    PersonUpdate,
    RelationshipCreate,
    RelationshipEvidenceCreate,
    RelationshipUpdate,
    SNAGraphMetricResponse,
)


class TestKGSchemas(unittest.TestCase):
    """Test suite validating KG Pydantic schemas and business constraints."""

    # ----------------------------------------------------
    # Person Schema Tests
    # ----------------------------------------------------

    def test_person_create_valid(self):
        """Valid person creation succeeds with trimmed display name."""
        data = {
            "display_name": "  Howqua (Wu Guoying)  ",
            "canonical_name_en": "Wu Guoying",
            "canonical_name_zh": "伍国莹",
            "primary_role": "行商 (Security Merchant)",
            "birth_year": 1731,
            "death_year": 1800,
            "active_start": date(1782, 1, 1),
            "active_end": date(1800, 12, 31),
            "identity_status": IdentityStatus.ACCEPTED,
        }
        person = PersonCreate(**data)
        self.assertEqual(person.display_name, "Howqua (Wu Guoying)")
        self.assertEqual(person.canonical_name_zh, "伍国莹")
        self.assertEqual(person.identity_status, IdentityStatus.ACCEPTED)

    def test_person_empty_display_name_fails(self):
        """Empty or whitespace-only display_name raises validation error."""
        with self.assertRaises(ValidationError):
            PersonCreate(display_name="   ")

        with self.assertRaises(ValidationError):
            PersonCreate(display_name="")

    def test_person_invalid_lifespan_order_fails(self):
        """birth_year greater than death_year raises validation error."""
        with self.assertRaises(ValidationError):
            PersonCreate(
                display_name="Test Person",
                birth_year=1820,
                death_year=1790,
            )

    def test_person_invalid_active_dates_order_fails(self):
        """active_start later than active_end raises validation error."""
        with self.assertRaises(ValidationError):
            PersonCreate(
                display_name="Test Person",
                active_start=date(1795, 1, 1),
                active_end=date(1792, 1, 1),
            )

    def test_person_update_partial_fields(self):
        """PersonUpdate allows updating only designated fields."""
        update = PersonUpdate(canonical_name_zh="潘振承")
        self.assertEqual(update.canonical_name_zh, "潘振承")
        self.assertIsNone(update.display_name)
        self.assertIsNone(update.death_year)

    # ----------------------------------------------------
    # PersonMention Schema Tests
    # ----------------------------------------------------

    def test_mention_create_valid_unresolved(self):
        """PersonMention without person_id passes validation as unresolved."""
        data = {
            "block_id": 101,
            "raw_text": "Howqua",
            "temporal_date": date(1792, 10, 24),
            "temporal_year": 1792,
            "spatial_location": "Canton",
            "confidence": 0.95,
        }
        mention = PersonMentionCreate(**data)
        self.assertEqual(mention.raw_text, "Howqua")
        self.assertIsNone(mention.person_id)
        self.assertEqual(mention.resolution_status, ResolutionStatus.UNRESOLVED)
        self.assertEqual(mention.confidence, 0.95)

    def test_mention_empty_raw_text_fails(self):
        """Blank or empty raw_text is rejected."""
        with self.assertRaises(ValidationError):
            PersonMentionCreate(block_id=1, raw_text="   ")

    def test_mention_invalid_confidence_fails(self):
        """Confidence outside [0, 1] range is rejected."""
        with self.assertRaises(ValidationError):
            PersonMentionCreate(block_id=1, raw_text="Browne", confidence=1.2)

        with self.assertRaises(ValidationError):
            PersonMentionCreate(block_id=1, raw_text="Browne", confidence=-0.1)

    def test_mention_update_resolution(self):
        """Resolving a mention updates person_id and status cleanly."""
        update = PersonMentionUpdate(
            person_id=42,
            resolution_status=ResolutionStatus.RESOLVED,
            review_status=ReviewStatusKG.REVIEWED,
        )
        self.assertEqual(update.person_id, 42)
        self.assertEqual(update.resolution_status, ResolutionStatus.RESOLVED)

    # ----------------------------------------------------
    # Relationship & Evidence Schema Tests
    # ----------------------------------------------------

    def test_relationship_create_valid(self):
        """Valid relationship creation with controlled type and non-negative weight."""
        rel = RelationshipCreate(
            source_person_id=1,
            target_person_id=2,
            relation_type=RelationType.COMMERCIAL,
            relation_label="Security Merchant surety",
            directionality=Directionality.DIRECTED,
            weight=12.5,
            valid_from=date(1792, 9, 1),
            valid_to=date(1793, 3, 1),
            confidence=0.9,
        )
        self.assertEqual(rel.relation_type, RelationType.COMMERCIAL)
        self.assertEqual(rel.weight, 12.5)

    def test_relationship_negative_weight_fails(self):
        """Negative weight raises validation error."""
        with self.assertRaises(ValidationError):
            RelationshipCreate(
                source_person_id=1,
                target_person_id=2,
                relation_type=RelationType.DEBT,
                weight=-5.0,
            )

    def test_relationship_invalid_date_order_fails(self):
        """valid_from after valid_to raises validation error."""
        with self.assertRaises(ValidationError):
            RelationshipCreate(
                source_person_id=1,
                target_person_id=2,
                relation_type=RelationType.COMMERCIAL,
                valid_from=date(1794, 1, 1),
                valid_to=date(1792, 1, 1),
            )

    def test_relationship_evidence_create(self):
        """RelationshipEvidenceCreate links to block and optionally to mention."""
        ev = RelationshipEvidenceCreate(
            block_id=5,
            person_mention_id=12,
            evidence_role=EvidenceRole.SUPPORTS,
            quote_text="...contract sealed by Mowqua...",
        )
        self.assertEqual(ev.block_id, 5)
        self.assertEqual(ev.evidence_role, EvidenceRole.SUPPORTS)

    # ----------------------------------------------------
    # ExternalAuthority Schema Tests
    # ----------------------------------------------------

    def test_external_authority_create_with_or_without_id(self):
        """ExternalAuthority supports both database ID and literature-only citation."""
        auth_db = ExternalAuthorityCreate(
            person_id=1,
            authority_source=AuthoritySource.CBDB,
            external_id="0018423",
            match_confidence=0.98,
        )
        self.assertEqual(auth_db.authority_source, AuthoritySource.CBDB)
        self.assertEqual(auth_db.external_id, "0018423")

        # Literature-only without external_id
        auth_lit = ExternalAuthorityCreate(
            person_id=1,
            authority_source=AuthoritySource.LIANG_CANTON,
            record_title="梁嘉彬《广东十三行考》卷二",
            historical_notes="记载怡和行创办年代与历代浩官更迭谱系",
            match_confidence=0.85,
            claim_status=ClaimStatus.ACCEPTED,
        )
        self.assertIsNone(auth_lit.external_id)
        self.assertIsNotNone(auth_lit.historical_notes)

    def test_external_authority_confidence_bounds(self):
        """match_confidence outside [0, 1] is rejected."""
        with self.assertRaises(ValidationError):
            ExternalAuthorityCreate(
                person_id=1,
                authority_source=AuthoritySource.WIKIDATA,
                match_confidence=1.05,
            )


if __name__ == "__main__":
    unittest.main()
