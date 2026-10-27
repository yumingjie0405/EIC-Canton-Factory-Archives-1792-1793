"""Comprehensive test suite for Stage 3: Person Disambiguation and Knowledge Fusion."""
from datetime import date
import unittest

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.test_support.db_fixtures import (
    drop_test_schema,
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.models.block import Block, ReviewStatus as BlockReviewStatus
from app.models.kg import (
    AuthoritySource,
    ClaimStatus,
    Directionality,
    IdentityStatus,
    MentionType,
    Person,
    PersonMention,
    RelationType,
    Relationship,
    RelationshipEvidence,
    ResolutionStatus,
    ReviewStatusKG,
    EntityOperationAudit,
)
from app.services.kg import (
    name_normalizer,
    candidate_generator,
    entity_matcher,
    authority_linker,
    disambiguation_service,
    entity_merge_service,
)


@requires_postgres_test_db
class TestKGDisambiguation(unittest.TestCase):
    """Test suite covering Stage 3 normalizer, generator, matcher, review, authority, and merges."""

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

        # Clean existing test kg records safely
        self.db.query(EntityOperationAudit).delete()
        self.db.query(RelationshipEvidence).delete()
        self.db.query(Relationship).delete()
        self.db.query(PersonMention).delete()
        self.db.query(Person).delete()
        self.db.commit()

        # Create base test Document, Page, Block
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
            ocr_text_raw="Supercargo Henry Browne met with Howqua at the British Factory.",
            ocr_text_corrected="Supercargo Henry Browne met with Howqua at the British Factory.",
            review_status=BlockReviewStatus.APPROVED,
        )
        self.db.add(self.test_block)
        self.db.commit()

    def tearDown(self):
        """Clean up test fixtures."""
        try:
            self.db.rollback()
            self.db.query(EntityOperationAudit).delete()
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
    # 1. Name Normalizer & Variant Management Tests
    # ----------------------------------------------------

    def test_normalize_for_matching_strips_titles_and_honorifics(self):
        """Normalizer produces clean comparison string by stripping titles without mutating raw string."""
        self.assertEqual(name_normalizer.normalize_for_matching("Supercargo Henry Browne"), "henry browne")
        self.assertEqual(name_normalizer.normalize_for_matching("Mr. Browne"), "browne")
        self.assertEqual(name_normalizer.normalize_for_matching("Capt. Jno. Wilson, esq."), "jno wilson")
        self.assertEqual(name_normalizer.normalize_for_matching("George Lord Macartney"), "george lord macartney")
        self.assertEqual(name_normalizer.normalize_for_matching("Lord Macartney"), "macartney")
        self.assertEqual(name_normalizer.normalize_for_matching("Matthew Rapier, jun."), "matthew rapier")

    def test_name_variants_preserves_raw_and_tracks_provenance(self):
        """Extract variants preserves exact raw form, title-stripped form, and spacing variants."""
        variants = name_normalizer.extract_variants("How Qua", provenance="archive_consultations")
        types = {v["variant_type"] for v in variants}
        forms = {v["form"] for v in variants}

        self.assertIn("exact", types)
        self.assertIn("collapsed_spacing", types)
        self.assertIn("How Qua", forms)
        self.assertIn("howqua", forms)

    # ----------------------------------------------------
    # 2. Candidate Generation & Hard Temporal Filtering Tests
    # ----------------------------------------------------

    def test_temporal_conflict_filters_out_deceased_figures(self):
        """Candidate generator filters out historical figures whose death predates the archive mention."""
        # Figure deceased in 1780 (cannot be candidate for 1792 document)
        deceased_person = Person(
            display_name="Puankhequa I (Pan Zhencheng)",
            canonical_name_en="Pan Zhencheng",
            primary_role="Security Merchant",
            birth_year=1714,
            death_year=1788,  # Died in 1788
        )
        # Figure living in 1792
        active_person = Person(
            display_name="Puankhequa II (Pan Youdu)",
            canonical_name_en="Pan Youdu",
            aliases=["Puankhequa", "Puan Khequa"],
            primary_role="Security Merchant",
            birth_year=1755,
            death_year=1820,
        )
        self.db.add_all([deceased_person, active_person])
        self.db.commit()

        # Mention dated 1792
        mention_1792 = PersonMention(
            block_id=self.test_block.id,
            raw_text="Puankhequa",
            normalized_text="puankhequa",
            temporal_year=1792,
            temporal_date=date(1792, 10, 15),
            resolution_status=ResolutionStatus.UNRESOLVED,
        )
        self.db.add(mention_1792)
        self.db.commit()

        candidates = candidate_generator.generate_candidates(db=self.db, mention=mention_1792)
        cand_persons = [p.display_name for p, score in candidates]

        # Puankhequa II should be recalled, Puankhequa I (died 1788) MUST be excluded by hard temporal constraint
        self.assertIn("Puankhequa II (Pan Youdu)", cand_persons)
        self.assertNotIn("Puankhequa I (Pan Zhencheng)", cand_persons)

    # ----------------------------------------------------
    # 3. Multi-Dimensional Explainable Matching Tests
    # ----------------------------------------------------

    def test_entity_matcher_detects_contradictions_and_penalizes(self):
        """EntityMatcher gives high total score for congruent evidence and suppresses score on contradictions."""
        person_browne = Person(
            display_name="Henry Browne",
            primary_role="Supercargo",
            base_location="Canton",
            birth_year=1754,
            death_year=1812,
        )
        self.db.add(person_browne)
        self.db.commit()

        # Congruent mention
        m_congruent = PersonMention(
            block_id=self.test_block.id,
            raw_text="Mr. Browne",
            normalized_text="browne",
            temporal_year=1792,
            spatial_location="Canton",
            evidence_text="Supercargo Browne presided over the consultation at Canton.",
        )
        self.db.add(m_congruent)
        self.db.commit()

        eval_good = entity_matcher.evaluate_match(db=self.db, mention=m_congruent, candidate=person_browne)
        self.assertGreaterEqual(eval_good.name_score, 0.85)
        self.assertGreaterEqual(eval_good.temporal_score, 0.70)
        self.assertGreaterEqual(eval_good.spatial_score, 0.80)
        self.assertEqual(len(eval_good.contradiction_flags), 0)
        self.assertIn(eval_good.recommendation, ["confirm", "needs_review"])

        # Mention with temporal contradiction (year 1825 > death 1812)
        m_conflict = PersonMention(
            block_id=self.test_block.id,
            raw_text="Henry Browne",
            normalized_text="henry browne",
            temporal_year=1825,
            spatial_location="Canton",
        )
        self.db.add(m_conflict)
        self.db.commit()

        eval_bad = entity_matcher.evaluate_match(db=self.db, mention=m_conflict, candidate=person_browne)
        self.assertEqual(eval_bad.temporal_score, 0.0)
        self.assertGreaterEqual(len(eval_bad.contradiction_flags), 1)
        self.assertEqual(eval_bad.recommendation, "reject")

    # ----------------------------------------------------
    # 4. Human-in-the-Loop Review & Decision State Tests
    # ----------------------------------------------------

    def test_confirm_reject_and_defer_workflow(self):
        """Human review operations transition resolution status and create immutable audit logs."""
        person = Person(display_name="Henry Browne", primary_role="Supercargo")
        self.db.add(person)
        self.db.commit()

        mention = PersonMention(
            block_id=self.test_block.id,
            raw_text="Mr. Browne",
            resolution_status=ResolutionStatus.UNRESOLVED,
        )
        self.db.add(mention)
        self.db.commit()

        # Confirm resolution
        disambiguation_service.confirm_resolution(
            db=self.db,
            mention_id=mention.id,
            person_id=person.id,
            reviewer="Dr. Historian",
            reason="Confirmed based on Select Committee diaries 1792",
        )

        self.db.refresh(mention)
        self.assertEqual(mention.person_id, person.id)
        self.assertEqual(mention.resolution_status, ResolutionStatus.RESOLVED)
        self.assertEqual(mention.review_status, ReviewStatusKG.REVIEWED)

        # Audit entry logged
        audits = self.db.query(EntityOperationAudit).filter_by(operation_type="confirm_link").all()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].operator, "Dr. Historian")
        self.assertEqual(audits[0].primary_person_id, person.id)

        # Defer resolution
        disambiguation_service.defer_resolution(
            db=self.db,
            mention_id=mention.id,
            reviewer="Dr. Historian",
            reason="Pending further checking with IOR records",
        )
        self.db.refresh(mention)
        self.assertEqual(mention.resolution_status, ResolutionStatus.UNCERTAIN)

    # ----------------------------------------------------
    # 5. External Authority Linking & Prior Seeding Tests
    # ----------------------------------------------------

    def test_seed_historical_priors_and_authority_linking(self):
        """Seeding priors registers canonical 1792 figures and links authoritative source records."""
        seeded = authority_linker.seed_historical_priors(db=self.db)
        self.assertGreaterEqual(len(seeded), 3)

        howqua = self.db.query(Person).filter(Person.display_name.ilike("%Howqua%")).first()
        self.assertIsNotNone(howqua)
        self.assertIsNotNone(howqua.aliases)

        # External authorities attached
        auths = howqua.external_authorities
        self.assertGreaterEqual(len(auths), 1)
        auth_sources = {a.authority_source for a in auths}
        self.assertIn(AuthoritySource.LIANG_CANTON, auth_sources)

    # ----------------------------------------------------
    # 6. Reversible Entity Merge and Split Tests
    # ----------------------------------------------------

    def test_reversible_merge_and_split(self):
        """Merging entities redirects mentions & relationships, and split restores original state completely."""
        primary = Person(display_name="Wu Bingjian", primary_role="Hong Merchant", aliases=["Howqua"])
        secondary = Person(display_name="Howqua the Younger", primary_role="Security Merchant", aliases=["Young Howqua"])
        self.db.add_all([primary, secondary])
        self.db.commit()

        # Create mention and relationship for secondary
        mention_sec = PersonMention(
            block_id=self.test_block.id,
            person_id=secondary.id,
            raw_text="Howqua the Younger",
        )
        rel_sec = Relationship(
            source_person_id=secondary.id,
            target_person_id=primary.id,  # Will be managed
            relation_type=RelationType.COMMERCIAL,
        )
        other_person = Person(display_name="Captain Wilson")
        self.db.add(other_person)
        self.db.commit()

        rel_out = Relationship(
            source_person_id=secondary.id,
            target_person_id=other_person.id,
            relation_type=RelationType.GUARANTEE,
        )
        self.db.add_all([mention_sec, rel_out])
        self.db.commit()

        # Step A: Preview Merge
        preview = entity_merge_service.preview_merge(
            db=self.db, primary_person_id=primary.id, secondary_person_id=secondary.id
        )
        self.assertTrue(preview["can_merge"])
        self.assertEqual(preview["impact"]["mentions_to_relink"], 1)

        # Step B: Execute Merge
        merge_audit = entity_merge_service.execute_merge(
            db=self.db,
            primary_person_id=primary.id,
            secondary_person_id=secondary.id,
            operator="lead_historian",
            reason="Confirmed identical historical actor in E-wo Hong",
        )
        self.assertIsNotNone(merge_audit.id)

        # Check secondary mentions redirected to primary
        self.db.refresh(mention_sec)
        self.assertEqual(mention_sec.person_id, primary.id)

        # Check relationship redirected
        self.db.refresh(rel_out)
        self.assertEqual(rel_out.source_person_id, primary.id)

        # Secondary status is REJECTED/MERGED
        self.db.refresh(secondary)
        self.assertEqual(secondary.identity_status, IdentityStatus.REJECTED)

        # Primary aliases updated
        self.db.refresh(primary)
        self.assertIn("Howqua the Younger", primary.aliases)

        # Step C: Reversibly Rollback / Split
        split_audit = entity_merge_service.split_or_rollback_merge(
            db=self.db,
            audit_id=merge_audit.id,
            operator="lead_historian",
            reason="New evidence from BL IOR proves these are two separate generations",
        )
        self.assertIsNotNone(split_audit.id)

        # Mention should be restored back to secondary person
        self.db.refresh(mention_sec)
        self.assertEqual(mention_sec.person_id, secondary.id)

        # Relationship restored back to secondary person
        self.db.refresh(rel_out)
        self.assertEqual(rel_out.source_person_id, secondary.id)

        # Secondary person status restored
        self.db.refresh(secondary)
        self.assertEqual(secondary.identity_status, IdentityStatus.PROVISIONAL)


if __name__ == "__main__":
    unittest.main()
