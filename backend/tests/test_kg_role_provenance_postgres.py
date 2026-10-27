"""PostgreSQL tests: role wording now reaches the database, with provenance.

Regression for the state where all 367 persons had ``primary_role IS NULL``:

* the extractor's ``role``/``organization`` reach both the mention (per occurrence) and
  the person created for the relationship endpoint;
* an existing non-empty role is never overwritten by a later block;
* provenance is recorded (``model_extracted``), so the UI can say how much to trust it;
* two same-named entities with different roles in one block attach nothing;
* the overview reports role counts for the persons actually rendered.

Only the database is exercised; no LLM call is made.
"""
import unittest

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.kg.enums import (
    ClaimStatus,
    IdentityStatus,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
    RoleSource,
)
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship
from app.models.page import Page
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.schemas.kg_extraction_schema import (
    ExtractedEntity,
    ExtractedRelationship,
    KGExtractionResult,
)
from app.schemas.research_group import GroupCreate
from app.services.kg import role_taxonomy
from app.services.kg.kg_writer import kg_writer
from app.services.kg.group_graph import load_scope, scope_overview
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-ROLE]"
GROUP = SYNTH + " Group"


@requires_postgres_test_db
class TestPersonRoleProvenancePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls.doc = cls._document("Synthetic_ROLE_doc.docx", 4)
        cls.group = cls.service.create_group(GroupCreate(name=GROUP))
        cls.db.add(
            ResearchGroupDocument(
                group_id=cls.group.id,
                document_id=cls.doc["document_id"],
                import_status=GroupImportStatus.READY,
                last_batch_id="role",
            )
        )
        cls.db.commit()

    @classmethod
    def _document(cls, filename, block_count):
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.DOCX,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        cls.db.add(document)
        cls.db.flush()
        page = Page(document_id=document.id, page_number=1, width=1000, height=1400)
        cls.db.add(page)
        cls.db.flush()
        block_ids = []
        for index in range(block_count):
            block = Block(
                page_id=page.id,
                block_index=index,
                block_label="paragraph",
                bbox=[0, 0, 10, 10],
                ocr_text_raw="%s archival text %d" % (SYNTH, index),
            )
            cls.db.add(block)
            cls.db.flush()
            block_ids.append(block.id)
        cls.db.commit()
        return {"document_id": document.id, "page_id": page.id, "block_ids": block_ids}

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            groups = [
                row[0]
                for row in db.query(ResearchGroup.id).filter(ResearchGroup.name.like(SYNTH + "%")).all()
            ]
            if groups:
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(groups)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(groups)).delete(
                    synchronize_session=False
                )
            docs = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_ROLE_%"))
                .all()
            ]
            pages = (
                [row[0] for row in db.query(Page.id).filter(Page.document_id.in_(docs))]
                if docs
                else []
            )
            blocks = (
                [row[0] for row in db.query(Block.id).filter(Block.page_id.in_(pages))]
                if pages
                else []
            )
            if blocks:
                db.query(PersonMention).filter(
                    PersonMention.block_id.in_(blocks)
                ).delete(synchronize_session=False)
            persons = [
                row[0]
                for row in db.query(Person.id).filter(Person.display_name.like("%" + SYNTH + "%")).all()
            ]
            if persons:
                db.query(Relationship).filter(
                    Relationship.source_person_id.in_(persons)
                    | Relationship.target_person_id.in_(persons)
                ).delete(synchronize_session=False)
                db.query(Person).filter(Person.id.in_(persons)).delete(synchronize_session=False)
            if pages:
                db.query(Page).filter(Page.id.in_(pages)).delete(synchronize_session=False)
            if docs:
                db.query(Document).filter(Document.id.in_(docs)).delete(synchronize_session=False)
            if blocks and pages:
                db.query(Block).filter(Block.id.in_(blocks)).delete(synchronize_session=False)
            db.commit()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures
            db.rollback()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        app.dependency_overrides.pop(get_db, None)
        cls.db.close()
        cls.engine.dispose()

    def _result(self, source, target, source_role, target_role, extra_entities=()):
        entities = [
            ExtractedEntity(
                mention=source,
                normalized_name=source,
                role=source_role,
                confidence=0.9,
                evidence="evidence " + source,
            ),
            ExtractedEntity(
                mention=target,
                normalized_name=target,
                role=target_role,
                confidence=0.9,
                evidence="evidence " + target,
            ),
            *extra_entities,
        ]
        return KGExtractionResult(
            entities=entities,
            relationships=[
                ExtractedRelationship(
                    source_mention=source,
                    target_mention=target,
                    relation_type="commercial",
                    relation_label="trade",
                    confidence=0.8,
                    evidence="block evidence",
                )
            ],
            temporal_date_hint="1792-10-24",
            spatial_location_hint="Canton",
        )

    # ------------------------------------------------------------------ tests
    def test_01_person_receives_the_extracted_role_with_provenance(self):
        result = self._result(
            "Security Merchant Howqua " + SYNTH,
            "Supercargo Browne " + SYNTH,
            "Security Merchant (Hong Merchant)",
            "Supercargo",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][0], result)
        self.db.commit()

        howqua = (
            self.db.query(Person)
            .filter(Person.display_name == "Security Merchant Howqua " + SYNTH)
            .one()
        )
        browne = (
            self.db.query(Person)
            .filter(Person.display_name == "Supercargo Browne " + SYNTH)
            .one()
        )
        self.assertEqual(howqua.primary_role, "Security Merchant (Hong Merchant)")
        self.assertEqual(howqua.role_source, RoleSource.MODEL_EXTRACTED.value)
        self.assertEqual(browne.primary_role, "Supercargo")
        self.assertEqual(
            role_taxonomy.classify_role(howqua.primary_role),
            role_taxonomy.ROLE_CATEGORY_HONG_MERCHANT,
        )
        self.assertEqual(
            role_taxonomy.classify_role(browne.primary_role),
            role_taxonomy.ROLE_CATEGORY_FOREIGN_MERCHANT,
        )

    def test_02_mentions_keep_their_own_role_wording(self):
        rows = (
            self.db.query(PersonMention)
            .filter(PersonMention.block_id == self.doc["block_ids"][0])
            .all()
        )
        self.assertTrue(rows)
        for mention in rows:
            self.assertTrue(mention.role_text)
            self.assertEqual(mention.role_source, RoleSource.MODEL_EXTRACTED.value)

    def test_03_existing_role_is_never_overwritten(self):
        person = (
            self.db.query(Person)
            .filter(Person.display_name == "Supercargo Browne " + SYNTH)
            .one()
        )
        person.primary_role = "Supercargo (President of Select Committee)"
        person.role_source = RoleSource.HUMAN_VERIFIED.value
        self.db.commit()

        result = self._result(
            "Supercargo Browne " + SYNTH,
            "Captain Sampton " + SYNTH,
            "ship captain",
            "Captain",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][1], result)
        self.db.commit()
        self.db.refresh(person)
        self.assertEqual(person.primary_role, "Supercargo (President of Select Committee)")
        self.assertEqual(person.role_source, RoleSource.HUMAN_VERIFIED.value)

    def test_04_empty_role_is_filled_in_without_touching_identity(self):
        person = (
            self.db.query(Person)
            .filter(Person.display_name == "Supercargo Browne " + SYNTH)
            .one()
        )
        person.primary_role = None
        person.role_source = None
        self.db.commit()
        person_id = person.id

        result = self._result(
            "Supercargo Browne " + SYNTH,
            "Mr. Fairlie " + SYNTH,
            "Supercargo",
            "Supercargo",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][2], result)
        self.db.commit()
        self.db.refresh(person)
        self.assertEqual(person.id, person_id, "no second person may be created")
        self.assertEqual(person.primary_role, "Supercargo")
        self.assertEqual(person.role_source, RoleSource.MODEL_EXTRACTED.value)

    def test_05_ambiguous_same_name_roles_attach_nothing(self):
        # Two entities normalise to the same name with different roles in one block:
        # the writer must refuse to guess which role belongs to the relationship endpoint.
        extra = ExtractedEntity(
            mention="Ambiguous Person " + SYNTH,
            normalized_name="Ambiguous Person " + SYNTH,
            role="Mandarin",
            confidence=0.7,
            evidence="second occurrence",
        )
        result = self._result(
            "Ambiguous Person " + SYNTH,
            "Other Party " + SYNTH,
            "Security Merchant",
            "Supercargo",
            extra_entities=(extra,),
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][3], result)
        self.db.commit()
        person = (
            self.db.query(Person)
            .filter(Person.display_name == "Ambiguous Person " + SYNTH)
            .one()
        )
        self.assertIsNone(person.primary_role, "conflicting roles must not be resolved by guessing")

    def test_06_no_role_written_when_the_model_reports_none(self):
        result = self._result(
            "Silent Person " + SYNTH,
            "Quiet Person " + SYNTH,
            "",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][0], result)
        self.db.commit()
        person = (
            self.db.query(Person)
            .filter(Person.display_name == "Silent Person " + SYNTH)
            .one_or_none()
        )
        if person is not None:
            self.assertIsNone(person.primary_role)
            self.assertIsNone(person.role_source)
        mentions = (
            self.db.query(PersonMention)
            .filter(
                PersonMention.block_id == self.doc["block_ids"][0],
                PersonMention.raw_text.like("%Silent Person%")
                | PersonMention.raw_text.like("%Quiet Person%"),
            )
            .all()
        )
        self.assertTrue(mentions, "the block should carry the two role-less mentions")
        for mention in mentions:
            self.assertIsNone(mention.role_text)
            self.assertIsNone(mention.role_source)

    def test_07_overview_counts_only_real_categories(self):
        overview = scope_overview(self.db, load_scope(self.db, self.group.id))
        counts = overview["role_counts"]
        self.assertIn(role_taxonomy.ROLE_CATEGORY_HONG_MERCHANT, counts)
        self.assertIn(role_taxonomy.ROLE_CATEGORY_FOREIGN_MERCHANT, counts)
        known = overview["role_known_count"]
        unknown = overview["role_unknown_count"]
        self.assertEqual(
            known + unknown,
            len(overview["persons"]),
            "every rendered person is counted exactly once",
        )
        self.assertIn(RoleSource.MODEL_EXTRACTED.value, overview["role_source_counts"])

    def test_08_heuristic_source_is_labelled_as_such(self):
        person = (
            self.db.query(Person)
            .filter(Person.display_name == "Mr. Fairlie " + SYNTH)
            .one()
        )
        person.primary_role = "Hong merchant (candidate)"
        person.role_source = RoleSource.HEURISTIC.value
        self.db.commit()
        overview = scope_overview(self.db, load_scope(self.db, self.group.id))
        self.assertIn(RoleSource.HEURISTIC.value, overview["role_source_counts"])

    # ------------------------------------------------- identity key (duplicates)
    def _persons_named(self, fragment):
        return (
            self.db.query(Person)
            .filter(Person.display_name.like("%" + fragment + "%"))
            .order_by(Person.id.asc())
            .all()
        )

    def test_09_a_rank_abbreviation_variant_joins_the_existing_person(self):
        # "Cap." and "Capt." are the same rank in this corpus; before the identity key
        # each spelling became its own graph node, splitting the person's mentions.
        first = self._result(
            "Capt. Variant Rogers " + SYNTH,
            "Variant Counterparty " + SYNTH,
            "Supercargo",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][0], first)
        self.db.commit()

        second = self._result(
            "Cap. Variant Rogers " + SYNTH,
            "Variant Counterparty " + SYNTH,
            "Supercargo",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][1], second)
        self.db.commit()

        persons = self._persons_named("Variant Rogers")
        self.assertEqual(
            len(persons), 1, "one captain, one node: %s" % [p.display_name for p in persons]
        )
        self.assertEqual(persons[0].display_name, "Capt. Variant Rogers " + SYNTH)
        aliases = persons[0].aliases or []
        self.assertIn("Cap. Variant Rogers " + SYNTH, aliases)

    def test_10_leading_article_variant_joins_the_existing_person(self):
        first = self._result(
            "Variant Hoppe " + SYNTH,
            "Variant Counterparty " + SYNTH,
            "",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][0], first)
        self.db.commit()

        second = self._result(
            "the Variant Hoppe " + SYNTH,
            "Variant Counterparty " + SYNTH,
            "",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][1], second)
        self.db.commit()

        persons = self._persons_named("Variant Hoppe")
        self.assertEqual(
            len(persons), 1, "the article is not part of the identity: %s"
            % [p.display_name for p in persons]
        )

    def test_11_lookalike_names_are_never_folded_together(self):
        # Same rank, different surname: a similarity threshold would merge these, the
        # identity key must not.
        for index, name in enumerate(("Mr. Variant Hay " + SYNTH, "Mr. Variant May " + SYNTH)):
            result = self._result(
                name,
                "Variant Counterparty " + SYNTH,
                "Supercargo",
                "",
            )
            kg_writer.write_extraction_result(self.db, self.doc["block_ids"][index], result)
            self.db.commit()

        self.assertEqual(len(self._persons_named("Variant Hay")), 1)
        self.assertEqual(len(self._persons_named("Variant May")), 1)
        self.assertNotEqual(
            self._persons_named("Variant Hay")[0].id,
            self._persons_named("Variant May")[0].id,
        )

    # ------------------------------------ shared identity key (Stage 10C.1 audit)
    def _person(self, display_name, *, aliases=None, role=None):
        person = Person(
            display_name=display_name,
            canonical_name_en=display_name,
            aliases=aliases,
            primary_role=role,
            identity_status=IdentityStatus.PROVISIONAL,
            review_status=ReviewStatusKG.UNREVIEWED,
        )
        self.db.add(person)
        self.db.commit()
        return person

    def test_12_a_shared_identity_key_never_silently_picks_the_oldest_record(self):
        # Two records share the key "colliding hughes synth": an honourific difference
        # ("Sir" vs "Mr.") is not proof that they are one man, so a third spelling must
        # not be attached to either of them.
        oldest = self._person("Sir Colliding Hughes " + SYNTH)
        newer = self._person("Mr. Colliding Hughes " + SYNTH)
        self.assertNotEqual(oldest.id, newer.id)

        variant = "Capt. Colliding Hughes " + SYNTH
        result = self._result(variant, "Variant Counterparty " + SYNTH, "Supercargo", "")
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][2], result)
        self.db.commit()

        self.db.refresh(oldest)
        self.db.refresh(newer)
        self.assertEqual(
            self.db.query(PersonMention).filter(PersonMention.person_id == oldest.id).count(),
            0,
            "the oldest record must not receive the ambiguous occurrence",
        )
        self.assertEqual(
            self.db.query(PersonMention).filter(PersonMention.person_id == newer.id).count(),
            0,
            "the newer record must not receive the ambiguous occurrence either",
        )
        self.assertIsNone(oldest.aliases, "no identity fold may be recorded on a guess")
        self.assertIsNone(newer.aliases)

        created = self._persons_named("Colliding Hughes")
        unresolved = [p for p in created if p.display_name == variant]
        self.assertEqual(
            len(unresolved), 1, "the spelling must be kept in its own unresolved record"
        )
        note = unresolved[0].biographical_notes or ""
        self.assertIn(str(oldest.id), note)
        self.assertIn(str(newer.id), note)

        relationship = (
            self.db.query(Relationship)
            .filter(Relationship.source_person_id == unresolved[0].id)
            .one_or_none()
        )
        self.assertIsNotNone(
            relationship, "the occurrence keeps its relationship instead of being dropped"
        )

    def test_13_existing_aliases_are_preserved_when_a_variant_is_absorbed(self):
        keeper = self._person("Alias Keeper " + SYNTH, aliases=["Legacy Alias"])
        result = self._result(
            "Cap. Alias Keeper " + SYNTH,
            "Variant Counterparty " + SYNTH,
            "Supercargo",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][3], result)
        self.db.commit()

        self.db.refresh(keeper)
        self.assertEqual(self._db_persons_named("Alias Keeper")[0].id, keeper.id)
        self.assertIn("Legacy Alias", keeper.aliases)
        self.assertIn("Cap. Alias Keeper " + SYNTH, keeper.aliases)
        self.assertEqual(keeper.display_name, "Alias Keeper " + SYNTH)

    def _db_persons_named(self, fragment):
        return (
            self.db.query(Person)
            .filter(Person.display_name.like("%" + fragment + "%"))
            .order_by(Person.id.asc())
            .all()
        )

    def test_14_a_single_candidate_is_still_matched_without_creating_a_record(self):
        only = self._person("Unique Merchant " + SYNTH)
        result = self._result(
            "the Unique Merchant " + SYNTH,
            "Variant Counterparty " + SYNTH,
            "Supercargo",
            "",
        )
        kg_writer.write_extraction_result(self.db, self.doc["block_ids"][0], result)
        self.db.commit()

        matches = self._db_persons_named("Unique Merchant")
        self.assertEqual([p.id for p in matches], [only.id])
        self.db.refresh(only)
        self.assertIn("the Unique Merchant " + SYNTH, only.aliases or [])
        self.assertIsNone(only.biographical_notes, "an unambiguous match needs no warning note")


if __name__ == "__main__":
    unittest.main()
