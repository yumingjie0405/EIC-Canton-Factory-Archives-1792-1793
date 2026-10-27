"""PostgreSQL tests: dynamic filter facets for the group graph.

The facets must describe **exactly** the subgraph the overview renders:
same scope, same view, same filter semantics. Each case below therefore checks the
facet numbers against the overview result instead of trusting the numbers alone.

Covered: empty group, only undated relationships, mixed dated/undated, only 1792, only
1793, multi-year spans, a year with no matches (a real empty result, never a fallback),
view switching (official vs candidates), relation-type filtering, unknown group (404),
two groups side by side, and mock provenance being reported rather than implied.
"""
import unittest
from datetime import date

from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentType
from app.models.kg.enums import (
    ClaimStatus,
    EvidenceRole,
    IdentityStatus,
    MentionType,
    RelationType,
    ResolutionStatus,
    ReviewStatusKG,
)
from app.models.kg.extraction_run import KGExtractionItem, KGExtractionRun
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.models.page import Page
from app.models.research_group import ResearchGroup, ResearchGroupDocument
from app.schemas.research_group import GroupCreate
from app.services.research_group_service import ResearchGroupService
from app.test_support.db_fixtures import (
    make_session_factory,
    make_test_engine,
    requires_postgres_test_db,
)

SYNTH = "[SYNTH-FAC]"
GROUP_MIXED = SYNTH + " Mixed dates group"
GROUP_UNDATED = SYNTH + " Undated only group"
GROUP_EMPTY = SYNTH + " Empty facets group"
GROUP_1793 = SYNTH + " Only 1793 group"


@requires_postgres_test_db
class TestGraphFacetsPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine()
        cls.db = make_session_factory(cls.engine)()
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)
        cls.service = ResearchGroupService(cls.db)
        cls._cleanup()
        cls._build_mixed()
        cls._build_undated()
        cls._build_1793()
        cls.group_empty = cls.service.create_group(GroupCreate(name=GROUP_EMPTY))

    @classmethod
    def tearDownClass(cls):
        try:
            cls._cleanup()
        finally:
            app.dependency_overrides.pop(get_db, None)
            cls.db.close()
            cls.engine.dispose()

    # ------------------------------------------------------------- builders
    @classmethod
    def _document(cls, filename, block_count=2):
        document = Document(
            filename=filename,
            original_filename=filename,
            file_path="storage/uploads/" + filename,
            file_type=DocumentType.DOCX,
            file_size=1024,
            page_count=1,
        )
        cls.db.add(document)
        cls.db.flush()
        page = Page(document_id=document.id, page_number=1, width=1000, height=1400)
        cls.db.add(page)
        cls.db.flush()
        blocks = []
        for index in range(block_count):
            block = Block(
                page_id=page.id,
                block_index=index,
                block_label="paragraph",
                bbox=[0, 0, 10, 10],
                ocr_text_raw="%s text %d" % (SYNTH, index),
            )
            cls.db.add(block)
            cls.db.flush()
            blocks.append(block)
        cls.db.commit()
        return document, blocks

    @classmethod
    def _group(cls, name, document_ids):
        group = cls.service.create_group(GroupCreate(name=name))
        for document_id in document_ids:
            cls.db.add(ResearchGroupDocument(group_id=group.id, document_id=document_id))
        cls.db.commit()
        return group

    @classmethod
    def _person(cls, suffix, identity=IdentityStatus.PROVISIONAL):
        person = Person(
            display_name="%s %s" % (SYNTH, suffix),
            canonical_name_en="%s %s" % (SYNTH, suffix),
            identity_status=identity,
        )
        cls.db.add(person)
        cls.db.commit()
        return person

    @classmethod
    def _mention(cls, block_id, person):
        cls.db.add(
            PersonMention(
                block_id=block_id,
                person_id=person.id,
                raw_text=person.display_name,
                normalized_text=person.display_name,
                mention_type=MentionType.PERSON_NAME,
                resolution_status=ResolutionStatus.RESOLVED,
                review_status=ReviewStatusKG.UNREVIEWED,
                temporal_year=1792,
            )
        )
        cls.db.commit()

    @classmethod
    def _relationship(
        cls, source, target, block_id, *, relation_type, claim, valid_from=None, valid_to=None
    ):
        relationship = Relationship(
            source_person_id=source.id,
            target_person_id=target.id,
            relation_type=relation_type,
            claim_status=claim,
            review_status=(
                ReviewStatusKG.REVIEWED
                if claim == ClaimStatus.ACCEPTED
                else ReviewStatusKG.UNREVIEWED
            ),
            confidence=0.8,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_summary=SYNTH + " synthetic",
        )
        cls.db.add(relationship)
        cls.db.flush()
        cls.db.add(
            RelationshipEvidence(
                relationship_id=relationship.id,
                block_id=block_id,
                quote_text=SYNTH + " quote",
                evidence_role=EvidenceRole.SUPPORTS,
            )
        )
        cls.db.commit()
        return relationship

    @classmethod
    def _run(cls, group_id, *, model, processed=1):
        run = KGExtractionRun(
            scope_type="group",
            scope_id=group_id,
            group_id=group_id,
            status="completed",
            model=model,
            trigger="manual",
            budget_blocks=10,
            budget_source="explicit",
            total_blocks=processed,
            processed_blocks=processed,
        )
        cls.db.add(run)
        cls.db.commit()
        return run

    @classmethod
    def _build_mixed(cls):
        cls.doc_mixed, blocks = cls._document("Synthetic_FAC_mixed.docx", 2)
        cls.group_mixed = cls._group(GROUP_MIXED, [cls.doc_mixed.id])
        b1, b2 = blocks
        cls.b1 = b1
        p1 = cls._person("Browne", IdentityStatus.ACCEPTED)
        p2 = cls._person("Howqua", IdentityStatus.ACCEPTED)
        p3 = cls._person("Puankhequa", IdentityStatus.ACCEPTED)
        p4 = cls._person("Gower", IdentityStatus.ACCEPTED)
        for person in (p1, p2, p3, p4):
            cls._mention(b1.id, person)
        # accepted + dated 1792 only
        cls.rel_1792 = cls._relationship(
            p1,
            p2,
            b1.id,
            relation_type=RelationType.COMMERCIAL,
            claim=ClaimStatus.ACCEPTED,
            valid_from=date(1792, 3, 1),
            valid_to=date(1792, 6, 1),
        )
        # asserted + spans 1792..1793
        cls.rel_span = cls._relationship(
            p2,
            p3,
            b1.id,
            relation_type=RelationType.COMMERCIAL,
            claim=ClaimStatus.ASSERTED,
            valid_from=date(1792, 5, 1),
            valid_to=date(1793, 2, 1),
        )
        # asserted + undated
        cls.rel_undated = cls._relationship(
            p3,
            p4,
            b2.id,
            relation_type=RelationType.DIPLOMATIC,
            claim=ClaimStatus.ASSERTED,
        )
        # accepted + dated 1793 only
        cls.rel_1793 = cls._relationship(
            p4,
            p1,
            b2.id,
            relation_type=RelationType.GUARANTEE,
            claim=ClaimStatus.ACCEPTED,
            valid_from=date(1793, 4, 1),
        )
        cls.mock_run = cls._run(cls.group_mixed.id, model="mock-model", processed=2)

    @classmethod
    def _build_undated(cls):
        doc, blocks = cls._document("Synthetic_FAC_undated.docx", 1)
        cls.group_undated = cls._group(GROUP_UNDATED, [doc.id])
        a = cls._person("Undated A")
        b = cls._person("Undated B", IdentityStatus.ACCEPTED)
        cls._mention(blocks[0].id, a)
        cls._mention(blocks[0].id, b)
        cls.rel_only_undated = cls._relationship(
            a,
            b,
            blocks[0].id,
            relation_type=RelationType.CORRESPONDENCE,
            claim=ClaimStatus.ASSERTED,
        )
        cls._run(cls.group_undated.id, model="mock-model", processed=1)

    @classmethod
    def _build_1793(cls):
        doc, blocks = cls._document("Synthetic_FAC_1793.docx", 1)
        cls.group_1793 = cls._group(GROUP_1793, [doc.id])
        a = cls._person("Late A", IdentityStatus.ACCEPTED)
        b = cls._person("Late B", IdentityStatus.ACCEPTED)
        cls._mention(blocks[0].id, a)
        cls._relationship(
            a,
            b,
            blocks[0].id,
            relation_type=RelationType.DEBT,
            claim=ClaimStatus.ACCEPTED,
            valid_from=date(1793, 7, 1),
        )
        # A real-model run: no mock provenance must be reported for this group.
        cls._run(cls.group_1793.id, model="some-real-model", processed=1)

    @classmethod
    def _cleanup(cls):
        db = cls.db
        try:
            group_ids = [
                row[0]
                for row in db.query(ResearchGroup.id)
                .filter(ResearchGroup.name.like(SYNTH + "%"))
                .all()
            ]
            if group_ids:
                run_ids = [
                    row[0]
                    for row in db.query(KGExtractionRun.id)
                    .filter(KGExtractionRun.group_id.in_(group_ids))
                    .all()
                ]
                if run_ids:
                    db.query(KGExtractionItem).filter(
                        KGExtractionItem.run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(KGExtractionRun).filter(
                        KGExtractionRun.id.in_(run_ids)
                    ).delete(synchronize_session=False)
                db.query(ResearchGroupDocument).filter(
                    ResearchGroupDocument.group_id.in_(group_ids)
                ).delete(synchronize_session=False)
                db.query(ResearchGroup).filter(ResearchGroup.id.in_(group_ids)).delete(
                    synchronize_session=False
                )
            person_ids = [
                row[0]
                for row in db.query(Person.id)
                .filter(Person.display_name.like(SYNTH + "%"))
                .all()
            ]
            if person_ids:
                relationship_ids = [
                    row[0]
                    for row in db.query(Relationship.id)
                    .filter(
                        Relationship.source_person_id.in_(person_ids)
                        | Relationship.target_person_id.in_(person_ids)
                    )
                    .all()
                ]
                if relationship_ids:
                    db.query(RelationshipEvidence).filter(
                        RelationshipEvidence.relationship_id.in_(relationship_ids)
                    ).delete(synchronize_session=False)
                    db.query(Relationship).filter(
                        Relationship.id.in_(relationship_ids)
                    ).delete(synchronize_session=False)
                db.query(PersonMention).filter(
                    PersonMention.person_id.in_(person_ids)
                ).delete(synchronize_session=False)
                db.query(Person).filter(Person.id.in_(person_ids)).delete(
                    synchronize_session=False
                )
            doc_ids = [
                row[0]
                for row in db.query(Document.id)
                .filter(Document.original_filename.like("Synthetic_FAC_%"))
                .all()
            ]
            if doc_ids:
                page_ids = [
                    row[0] for row in db.query(Page.id).filter(Page.document_id.in_(doc_ids)).all()
                ]
                if page_ids:
                    db.query(Block).filter(Block.page_id.in_(page_ids)).delete(
                        synchronize_session=False
                    )
                    db.query(Page).filter(Page.id.in_(page_ids)).delete(
                        synchronize_session=False
                    )
                db.query(Document).filter(Document.id.in_(doc_ids)).delete(
                    synchronize_session=False
                )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    def _facets(self, group_id, query=""):
        response = self.client.get("/api/kg/groups/%d/graph/facets%s" % (group_id, query))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _overview(self, group_id, query=""):
        response = self.client.get(
            "/api/kg/groups/%d/graph/overview%s" % (group_id, query)
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------------ cases
    def test_01_empty_group_returns_empty_facets(self):
        facets = self._facets(self.group_empty.id)
        self.assertEqual(facets["relation_types"], [])
        self.assertEqual(facets["dated_years"], [])
        self.assertIsNone(facets["year_range"])
        self.assertEqual(facets["total_relationship_count"], 0)
        self.assertFalse(facets["time_filter_available"])
        self.assertTrue(facets["time_filter_reason"])

    def test_02_only_undated_relationships_disable_the_year_filter(self):
        facets = self._facets(self.group_undated.id)
        self.assertEqual(facets["dated_years"], [])
        self.assertEqual(facets["undated_relationship_count"], 1)
        self.assertEqual(facets["total_relationship_count"], 1)
        self.assertFalse(facets["time_filter_available"])
        self.assertIn("日期信息", facets["time_filter_reason"])
        self.assertEqual(
            [item["value"] for item in facets["relation_types"]], ["correspondence"]
        )

    def test_03_mixed_dates_report_years_and_undated_counts(self):
        facets = self._facets(self.group_mixed.id)
        years = {item["year"]: item["count"] for item in facets["dated_years"]}
        self.assertEqual(years, {1792: 2, 1793: 2})
        self.assertEqual(facets["undated_relationship_count"], 1)
        self.assertEqual(facets["total_relationship_count"], 4)
        self.assertTrue(facets["time_filter_available"])
        self.assertEqual(facets["year_range"], {"min": 1792, "max": 1793})
        types = {item["value"]: item["count"] for item in facets["relation_types"]}
        self.assertEqual(types, {"commercial": 2, "diplomatic": 1, "guarantee": 1})

    def test_04_facets_match_the_overview_for_every_view(self):
        for view in ("all", "official", "candidates"):
            facets = self._facets(self.group_mixed.id, "?view=%s" % view)
            overview = self._overview(self.group_mixed.id, "?view=%s" % view)
            self.assertEqual(
                sum(item["count"] for item in facets["relation_types"]),
                len(overview["relationships"]),
                view,
            )
            self.assertEqual(
                facets["total_relationship_count"], len(overview["relationships"]), view
            )
        official = self._facets(self.group_mixed.id, "?view=official")
        self.assertEqual(
            {item["value"]: item["count"] for item in official["relation_types"]},
            {"commercial": 1, "guarantee": 1},
        )
        self.assertEqual(official["undated_relationship_count"], 0)
        candidates = self._facets(self.group_mixed.id, "?view=candidates")
        self.assertEqual(
            {item["value"]: item["count"] for item in candidates["relation_types"]},
            {"commercial": 1, "diplomatic": 1},
        )
        self.assertEqual(candidates["undated_relationship_count"], 1)

    def test_05_only_1792_and_only_1793_groups(self):
        late = self._facets(self.group_1793.id)
        self.assertEqual([item["year"] for item in late["dated_years"]], [1793])
        self.assertEqual(late["year_range"], {"min": 1793, "max": 1793})
        mixed = self._facets(self.group_mixed.id)
        self.assertIn(1792, [item["year"] for item in mixed["dated_years"]])

    def test_06_type_filter_keeps_type_counts_and_narrows_years(self):
        filtered = self._facets(self.group_mixed.id, "?relation_types=commercial")
        types = {item["value"]: item["count"] for item in filtered["relation_types"]}
        # Type counts ignore the type filter (otherwise the option list would collapse).
        self.assertEqual(types, {"commercial": 2, "diplomatic": 1, "guarantee": 1})
        years = {item["year"]: item["count"] for item in filtered["dated_years"]}
        # Year counts do apply the type filter: only the two commercial relations.
        self.assertEqual(years, {1792: 2, 1793: 1})
        self.assertEqual(filtered["undated_relationship_count"], 0)

        overview = self._overview(
            self.group_mixed.id, "?view=all&relation_types=commercial"
        )
        self.assertEqual(len(overview["relationships"]), 2)

    def test_07_year_filter_narrows_and_a_missing_year_is_really_empty(self):
        year_1793 = self._overview(
            self.group_mixed.id,
            "?view=all&period_start=1793-01-01&period_end=1793-12-31&include_unknown_dates=false",
        )
        self.assertEqual(
            {rel["id"] for rel in year_1793["relationships"]},
            {self.rel_span.id, self.rel_1793.id},
        )
        year_1792 = self._overview(
            self.group_mixed.id,
            "?view=all&period_start=1792-01-01&period_end=1792-12-31&include_unknown_dates=false",
        )
        self.assertEqual(
            {rel["id"] for rel in year_1792["relationships"]},
            {self.rel_1792.id, self.rel_span.id},
        )
        no_match = self._overview(
            self.group_mixed.id,
            "?view=all&period_start=1700-01-01&period_end=1700-12-31"
            "&include_unknown_dates=false&include_isolates=false",
        )
        self.assertEqual(no_match["relationships"], [])
        self.assertEqual(no_match["persons"], [])
        # Without the isolate toggle, persons mentioned in the group stay visible even
        # when every relation is filtered out — that is the documented semantics.
        with_isolates = self._overview(
            self.group_mixed.id,
            "?view=all&period_start=1700-01-01&period_end=1700-12-31&include_unknown_dates=false",
        )
        self.assertEqual(with_isolates["relationships"], [])
        self.assertTrue(with_isolates["persons"])

    def test_08_undated_relations_are_kept_by_default_and_excluded_on_request(self):
        with_undated = self._overview(self.group_mixed.id, "?view=all")
        self.assertIn(self.rel_undated.id, {rel["id"] for rel in with_undated["relationships"]})
        without_undated = self._overview(
            self.group_mixed.id,
            "?view=all&period_start=1792-01-01&period_end=1793-12-31&include_unknown_dates=false",
        )
        self.assertNotIn(
            self.rel_undated.id, {rel["id"] for rel in without_undated["relationships"]}
        )
        facets = self._facets(self.group_mixed.id)
        self.assertTrue(facets["include_unknown_dates"])
        self.assertEqual(facets["undated_relationship_count"], 1)

    def test_09_switching_groups_changes_the_facets(self):
        mixed = self._facets(self.group_mixed.id)
        late = self._facets(self.group_1793.id)
        undated = self._facets(self.group_undated.id)
        self.assertNotEqual(mixed["group_id"], late["group_id"])
        self.assertEqual({item["value"] for item in late["relation_types"]}, {"debt"})
        self.assertEqual({item["value"] for item in undated["relation_types"]}, {"correspondence"})

    def test_10_unknown_group_is_404(self):
        response = self.client.get("/api/kg/groups/999999/graph/facets")
        self.assertEqual(response.status_code, 404, response.text)

    def test_11_mock_provenance_is_reported(self):
        mock_facets = self._facets(self.group_mixed.id)
        self.assertTrue(mock_facets["data_trust"]["mock_derived"])
        self.assertIn(self.mock_run.id, mock_facets["data_trust"]["mock_run_ids"])
        self.assertIn("Mock", mock_facets["data_trust"]["note"])

        real_facets = self._facets(self.group_1793.id)
        self.assertFalse(real_facets["data_trust"]["mock_derived"])
        self.assertEqual(real_facets["data_trust"]["models"], ["some-real-model"])

        scope = self.client.get(
            "/api/kg/groups/%d/graph/scope" % self.group_mixed.id
        ).json()
        self.assertTrue(scope["data_trust"]["mock_derived"])

    def test_12_official_view_never_presents_asserted_mock_claims_as_evidence(self):
        official = self._overview(self.group_mixed.id, "?view=official")
        ids = {rel["id"] for rel in official["relationships"]}
        self.assertNotIn(self.rel_span.id, ids)  # asserted (mock-era candidate)
        self.assertNotIn(self.rel_undated.id, ids)
        self.assertEqual(ids, {self.rel_1792.id, self.rel_1793.id})
        facets = self._facets(self.group_mixed.id, "?view=official")
        self.assertEqual(facets["total_relationship_count"], 2)
        self.assertTrue(facets["data_trust"]["mock_derived"])

    def test_13_mention_years_are_reported_separately(self):
        facets = self._facets(self.group_mixed.id)
        self.assertEqual([item["year"] for item in facets["mention_years"]], [1792])
        self.assertIn("不等于关系成立年份", facets["mention_year_note"])
        # A mention year must never become a relationship year option.
        self.assertEqual(
            [item["year"] for item in facets["dated_years"]], [1792, 1793]
        )
