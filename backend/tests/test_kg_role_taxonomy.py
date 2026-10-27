"""Unit tests: role wording → category, and how much each source can be trusted.

The graph legend advertises role categories, so the mapping has to be deterministic and
honest:

* "no role recorded" is ``unknown``, never a catch-all "other";
* an ambassador stays a diplomat instead of becoming a generic official;
* a comprador is not silently folded into the interpreters;
* nothing is decided by fuzzy similarity.
"""
import unittest

from app.models.kg.enums import RoleSource
from app.services.kg import role_taxonomy as taxonomy


class RoleClassificationTests(unittest.TestCase):
    def test_hong_merchant_wording(self):
        for text in (
            "Security Merchant (Hong Merchant)",
            "security merchant",
            "Hong merchant",
            "行商",
            "保商",
        ):
            self.assertEqual(taxonomy.classify_role(text), taxonomy.ROLE_CATEGORY_HONG_MERCHANT, text)

    def test_foreign_merchant_wording(self):
        for text in (
            "Supercargo (President of Select Committee)",
            "supra cargo",
            "Captain",
            "大班",
        ):
            self.assertEqual(
                taxonomy.classify_role(text), taxonomy.ROLE_CATEGORY_FOREIGN_MERCHANT, text
            )

    def test_chinese_officials(self):
        for text in ("Viceroy", "Hoppo", "Mandarin", "Governor", "总督", "海关监督"):
            self.assertEqual(taxonomy.classify_role(text), taxonomy.ROLE_CATEGORY_OFFICIAL, text)

    def test_ambassador_is_kept_apart_from_ordinary_officials(self):
        # The project distinguishes the embassy from provincial posts; lumping them
        # would erase a real historical difference.
        for text in (
            "Ambassador Extraordinary and Plenipotentiary",
            "envoy to the court",
            "使节",
            "公使",
        ):
            self.assertEqual(taxonomy.classify_role(text), taxonomy.ROLE_CATEGORY_DIPLOMATIC, text)

    def test_comprador_is_not_an_interpreter(self):
        self.assertEqual(taxonomy.classify_role("Comprador"), taxonomy.ROLE_CATEGORY_COMPRADOR)
        self.assertEqual(taxonomy.classify_role("买办"), taxonomy.ROLE_CATEGORY_COMPRADOR)
        self.assertEqual(taxonomy.classify_role("Linguist"), taxonomy.ROLE_CATEGORY_INTERPRETER)
        self.assertEqual(taxonomy.classify_role("通事"), taxonomy.ROLE_CATEGORY_INTERPRETER)

    def test_missing_or_unrecognised_roles_are_unknown_not_other(self):
        for text in (None, "", "   ", "unknown person", "merchant of Venice"):
            self.assertEqual(taxonomy.classify_role(text), taxonomy.ROLE_CATEGORY_UNKNOWN, text)

    def test_matching_is_deterministic_and_ignores_case(self):
        self.assertEqual(
            taxonomy.classify_role("SECURITY MERCHANT"),
            taxonomy.classify_role("security merchant"),
        )

    def test_role_is_not_guessed_from_a_similar_string(self):
        # "merchant" alone is not enough to claim a Hong merchant.
        self.assertEqual(taxonomy.classify_role("merchant"), taxonomy.ROLE_CATEGORY_UNKNOWN)

    def test_is_recorded_role_distinguishes_empty_from_present(self):
        self.assertFalse(taxonomy.is_recorded_role(None))
        self.assertFalse(taxonomy.is_recorded_role("  "))
        self.assertTrue(taxonomy.is_recorded_role("Supercargo"))

    def test_labels_cover_every_category(self):
        for category in taxonomy.ROLE_CATEGORIES:
            self.assertTrue(taxonomy.role_category_label(category))
        self.assertEqual(
            taxonomy.role_category_label(None), taxonomy.ROLE_CATEGORY_LABELS["unknown"]
        )

    def test_source_labels_cover_every_enum_value(self):
        for source in RoleSource:
            self.assertTrue(taxonomy.role_source_label(source.value))
        self.assertTrue(taxonomy.role_source_label(None))

    def test_confidence_note_mentions_heuristic_and_model_separately(self):
        heuristic = taxonomy.confidence_note("Ambassador", RoleSource.HEURISTIC.value)
        model = taxonomy.confidence_note("Ambassador", RoleSource.MODEL_EXTRACTED.value)
        verified = taxonomy.confidence_note("Ambassador", RoleSource.HUMAN_VERIFIED.value)
        self.assertIn("推测", heuristic)
        self.assertIn("模型", model)
        self.assertIn("人工", verified)
        self.assertIn("没有可识别", taxonomy.confidence_note(None, None))


if __name__ == "__main__":
    unittest.main()
