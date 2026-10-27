"""Integration tests for Knowledge Graph database models and constraints."""
from datetime import date, datetime
import unittest

from sqlalchemy.exc import IntegrityError

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
    AuthoritySource,
    ClaimStatus,
    Directionality,
    EvidenceRole,
    ExternalAuthority,
    IdentityStatus,
    MentionType,
    Person,
    PersonMention,
    RelationType,
    Relationship,
    RelationshipEvidence,
    ResolutionStatus,
    ReviewStatusKG,
    SNAGraphMetric,
)


@requires_postgres_test_db
class TestKGModels(unittest.TestCase):
    """Test suite validating KG ORM models against a real database and constraints."""

    @classmethod
    def setUpClass(cls):
        # Test isolation: dedicated test database only (guard validated).
        cls.engine = make_test_engine()
        cls.session_factory = make_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        drop_test_schema(cls.engine)

    def setUp(self):
        """Prepare database session and clean test fixtures."""
        self.db = self.session_factory()

        # Create a test document hierarchy for block references
        self.test_doc = Document(
            filename="test_canton_diary_1792.pdf",
            original_filename="test_canton_diary_1792.pdf",
            file_path="storage/uploads/test_canton_diary_1792.pdf",
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        self.db.add(self.test_doc)
        self.db.commit()

        self.test_page = Page(
            document_id=self.test_doc.id,
            page_number=1,
            image_path="storage/pages/test_p1.png",
            width=1000,
            height=1400,
        )
        self.db.add(self.test_page)
        self.db.commit()

        self.test_block = Block(
            page_id=self.test_page.id,
            block_index=1,
            block_label="paragraph",
            bbox=[100, 150, 800, 250],
            ocr_text_raw="Howqua and Supercargo Browne met at the British Factory.",
            ocr_text_corrected="Howqua and Supercargo Browne met at the British Factory.",
            review_status=BlockReviewStatus.APPROVED,
        )
        self.db.add(self.test_block)
        self.db.commit()

    def tearDown(self):
        """Clean up test fixtures in reverse dependency order."""
        try:
            self.db.rollback()
            # Clean KG tables first
            self.db.query(SNAGraphMetric).delete()
            self.db.query(ExternalAuthority).delete()
            self.db.query(RelationshipEvidence).delete()
            self.db.query(Relationship).delete()
            self.db.query(PersonMention).delete()
            self.db.query(Person).delete()

            # Clean document tables
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

    def test_create_canonical_person(self):
        """Person entity can be persisted and retrieved with all historical attributes."""
        person = Person(
            display_name="Henry Browne",
            canonical_name_en="Henry Browne",
            primary_role="Supercargo (President of Select Committee)",
            organization="East India Company",
            active_start=date(1785, 1, 1),
            active_end=date(1795, 12, 31),
            base_location="Canton / Macao",
            identity_status=IdentityStatus.ACCEPTED,
            review_status=ReviewStatusKG.REVIEWED,
        )
        self.db.add(person)
        self.db.commit()
        self.assertIsNotNone(person.id)

        retrieved = self.db.query(Person).filter_by(id=person.id).first()
        self.assertEqual(retrieved.display_name, "Henry Browne")
        self.assertEqual(retrieved.identity_status, IdentityStatus.ACCEPTED)

    def test_mention_unresolved_creation(self):
        """Mention without linked person (person_id=None) persists successfully."""
        mention = PersonMention(
            block_id=self.test_block.id,
            person_id=None,
            raw_text="Howqua",
            normalized_text="Howqua",
            mention_type=MentionType.ALIAS,
            temporal_date=date(1792, 10, 24),
            temporal_year=1792,
            spatial_location="Canton",
            confidence=0.92,
            resolution_status=ResolutionStatus.UNRESOLVED,
            review_status=ReviewStatusKG.UNREVIEWED,
        )
        self.db.add(mention)
        self.db.commit()
        self.assertIsNotNone(mention.id)
        self.assertIsNone(mention.person_id)

    def test_mention_invalid_block_id_rejected_by_fk(self):
        """Mention pointing to a non-existent block_id is rejected by foreign key constraint."""
        mention = PersonMention(
            block_id=99999999,  # Non-existent
            raw_text="Ghost Person",
        )
        self.db.add(mention)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_relationship_and_evidence_graph_linking(self):
        """Relationship links two persons and traces back to archival block and mention evidence."""
        p1 = Person(display_name="Henry Browne", identity_status=IdentityStatus.ACCEPTED)
        p2 = Person(display_name="Howqua (Wu Guoying)", identity_status=IdentityStatus.ACCEPTED)
        self.db.add_all([p1, p2])
        self.db.commit()

        mention = PersonMention(
            block_id=self.test_block.id,
            person_id=p2.id,
            raw_text="Howqua",
            resolution_status=ResolutionStatus.RESOLVED,
        )
        self.db.add(mention)
        self.db.commit()

        rel = Relationship(
            source_person_id=p1.id,
            target_person_id=p2.id,
            relation_type=RelationType.COMMERCIAL,
            relation_label="Security merchant settlement",
            directionality=Directionality.DIRECTED,
            weight=1.0,
            valid_from=date(1792, 10, 1),
            confidence=0.95,
        )
        self.db.add(rel)
        self.db.commit()

        evidence = RelationshipEvidence(
            relationship_id=rel.id,
            person_mention_id=mention.id,
            block_id=self.test_block.id,
            evidence_role=EvidenceRole.SUPPORTS,
            quote_text="Howqua and Supercargo Browne met at the British Factory.",
        )
        self.db.add(evidence)
        self.db.commit()

        # Query back through ORM relationships
        rel_queried = self.db.query(Relationship).filter_by(id=rel.id).first()
        self.assertEqual(len(rel_queried.evidences), 1)
        self.assertEqual(rel_queried.evidences[0].block_id, self.test_block.id)
        self.assertEqual(rel_queried.evidences[0].person_mention_id, mention.id)

    def test_external_authority_without_external_id(self):
        """ExternalAuthority record can store literature citation without an external numeric ID."""
        person = Person(display_name="Puankhequa I (Pan Zhencheng)")
        self.db.add(person)
        self.db.commit()

        auth = ExternalAuthority(
            person_id=person.id,
            authority_source=AuthoritySource.LIANG_CANTON,
            external_id=None,
            record_title="梁嘉彬《广东十三行考》",
            historical_notes="同文行创办人，乾隆五十二年去世，由有度接掌并称二代潘启官",
            match_confidence=0.90,
            claim_status=ClaimStatus.ACCEPTED,
        )
        self.db.add(auth)
        self.db.commit()
        self.assertIsNotNone(auth.id)
        self.assertIsNone(auth.external_id)

    def test_sna_metric_unique_run_person_constraint(self):
        """SNAGraphMetric enforces unique (run_id, person_id) constraint."""
        person = Person(display_name="Lord Macartney")
        self.db.add(person)
        self.db.commit()

        metric1 = SNAGraphMetric(
            person_id=person.id,
            run_id="run_1792_mission_v1",
            graph_scope="embassy_diplomatic_network",
            degree_centrality=0.85,
            betweenness_centrality=0.72,
            closeness_centrality=0.90,
            algorithm_version="NetworkX_2.8_Louvain",
        )
        self.db.add(metric1)
        self.db.commit()

        # Duplicate insertion for same (run_id, person_id) should fail
        metric2 = SNAGraphMetric(
            person_id=person.id,
            run_id="run_1792_mission_v1",
            graph_scope="embassy_diplomatic_network",
            degree_centrality=0.88,
            algorithm_version="NetworkX_2.8_Louvain",
        )
        self.db.add(metric2)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_person_check_constraint_birth_death(self):
        """Database check constraint rejects birth_year > death_year."""
        person = Person(
            display_name="Anachronistic Person",
            birth_year=1850,
            death_year=1790,
        )
        self.db.add(person)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()


if __name__ == "__main__":
    unittest.main()
