"""Regression tests: document ids are not a stable temporal identity.

Root cause of the intermittent empty retrieval observed in R2.5: the curated
year override table is keyed by the primary key of the original archival import.
In any re-seeded database (a fresh test instance, a restored dump, a fixture
insert) the same id can belong to an unrelated file, so a document dated 1792
could be silently re-labelled with another volume's years and the strict
single-year filter then dropped it — producing "no evidence" for a question the
corpus can answer.

Pure: no database, no network, no Alembic.
"""
import unittest

from app.services.rag.temporal_resolver import YEAR_PATTERN, TemporalResolver, temporal_resolver


class TemporalIdentityTests(unittest.TestCase):
    """The filename of the stored document wins over a contradicting id override."""

    def setUp(self):
        self.resolver = TemporalResolver()

    def test_curated_override_still_applies_when_filename_agrees(self):
        info = self.resolver.resolve_document_temporal_info(
            55, "Canton_Consultations_1792-1793.pdf"
        )
        self.assertEqual((info["start_year"], info["end_year"]), (1792, 1793))
        self.assertEqual(info["provenance"], "human_verified")
        self.assertTrue(info["is_certain"])

    def test_filename_without_a_year_keeps_the_curated_override(self):
        info = self.resolver.resolve_document_temporal_info(43, "IOR_G_12_Consultations.pdf")
        self.assertEqual((info["start_year"], info["end_year"]), (1788, 1789))
        self.assertEqual(info["provenance"], "human_verified")

    def test_missing_filename_keeps_the_curated_override_for_legacy_rows(self):
        info = self.resolver.resolve_document_temporal_info(43, None)
        self.assertEqual((info["start_year"], info["end_year"]), (1788, 1789))
        self.assertEqual(info["provenance"], "human_verified")

    def test_collision_with_a_1792_document_falls_back_to_the_filename(self):
        """The R2.5 regression: id 43 is curated to 1788-1789 but holds a 1792 file."""
        info = self.resolver.resolve_document_temporal_info(
            43, "Synthetic_Consultations_1792.pdf"
        )
        self.assertEqual((info["start_year"], info["end_year"]), (1792, 1792))
        self.assertEqual(info["provenance"], "filename_inferred")
        self.assertTrue(self.resolver.is_document_covering_year(43, 1792, "Synthetic_Consultations_1792.pdf"))
        self.assertFalse(self.resolver.is_document_covering_year(43, 1788, "Synthetic_Consultations_1792.pdf"))

    def test_collision_with_a_year_outside_the_curated_range(self):
        info = self.resolver.resolve_document_temporal_info(572, "Synthetic_Consultations_1793.pdf")
        self.assertEqual((info["start_year"], info["end_year"]), (1793, 1793))
        self.assertEqual(info["provenance"], "filename_inferred")

    def test_unknown_document_stays_unknown(self):
        info = self.resolver.resolve_document_temporal_info(9999, "undated_ledger.pdf")
        self.assertIsNone(info["start_year"])
        self.assertEqual(info["provenance"], "unknown")
        self.assertFalse(info["is_certain"])
        self.assertFalse(self.resolver.is_document_covering_year(9999, 1792, "undated_ledger.pdf"))

    def test_multi_year_filename_yields_a_range(self):
        info = self.resolver.resolve_document_temporal_info(9999, "Consultations_1792-1793.pdf")
        self.assertEqual((info["start_year"], info["end_year"]), (1792, 1793))
        self.assertTrue(self.resolver.is_document_covering_year(9999, 1793, "Consultations_1792-1793.pdf"))

    def test_underscored_filenames_still_parse(self):
        self.assertEqual(YEAR_PATTERN.findall("IOR_G_12_1792_Consultations.pdf"), ["1792"])
        self.assertEqual(YEAR_PATTERN.findall("ledger_1179288.pdf"), [])

    def test_shared_singleton_uses_the_same_rule(self):
        info = temporal_resolver.resolve_document_temporal_info(
            43, "Synthetic_Consultations_1792.pdf"
        )
        self.assertEqual(info["start_year"], 1792)


if __name__ == "__main__":
    unittest.main()
