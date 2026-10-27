"""Unit tests for the person identity key (pure, no database).

The graph showed the same historical person as several nodes because the writer
compared raw display names, so every new spelling became a new person. These tests
pin the key that decides *who* a name refers to:

* differences that carry no identity information are folded (case, punctuation,
  hyphens, leading articles, rank abbreviations, honorifics, trailing ``Esq.``);
* look-alike spellings are **not** folded — ``Chowqua``/``Chowzua`` and
  ``Capt. Hay``/``Capt. May`` are identity questions for a human, not a similarity
  threshold for code.
"""
import unittest

from app.services.kg.name_normalizer import name_normalizer


class IdentityKeyTests(unittest.TestCase):
    def key(self, value):
        return name_normalizer.identity_key(value)

    def test_case_punctuation_and_hyphens_do_not_split_a_person(self):
        self.assertEqual(self.key("Pian-Khequa"), self.key("Pian Khequa"))
        self.assertEqual(self.key("Mr"), self.key("Mr."))
        self.assertEqual(self.key("Pian  Khequa "), self.key("pian khequa"))

    def test_leading_articles_do_not_split_a_person(self):
        for bare, articulated in (
            ("Hoppe", "the Hoppe"),
            ("Hoppo", "the Hoppo"),
            ("President", "The President"),
            ("Committee", "the Committee"),
            ("Merchants", "the Merchants"),
            ("Governor General", "the Governor General"),
        ):
            with self.subTest(name=bare):
                self.assertEqual(self.key(bare), self.key(articulated))

    def test_rank_abbreviations_do_not_split_a_person(self):
        rogers = [self.key(name) for name in (
            "Capt. Rogers",
            "Cap. Rogers",
            "Caps. Rogers",
            "Cap! Rogers",
            "Captain Rogers",
        )]
        self.assertEqual(len(set(rogers)), 1, rogers)
        self.assertEqual(self.key("Capt. John Rogers"), self.key("Captain John Rogers"))
        self.assertEqual(self.key("Cap'l. John banning"), self.key("Capt John banning"))

    def test_honorifics_and_suffixes_do_not_split_a_person(self):
        self.assertEqual(self.key("Henry Brown Esq."), self.key("Henry Brown"))
        self.assertEqual(self.key("Lord Macartney"), self.key("Macartney"))
        self.assertEqual(self.key("Capt. Henry Churchill"), self.key("Henry Churchill"))

    def test_lookalike_spellings_stay_separate(self):
        pairs = [
            ("Chowqua", "Chowzua"),
            ("Capt. Hay", "Capt. May"),
            ("Henry Brown Esq.", "Henry Browne"),
            ("Hoppe", "Hopps"),
            ("Pian Khequa", "Puan Chequa"),
            # an honorific that changes who is meant must not be folded away
            ("Mr. Harrison", "John Harrison"),
        ]
        for left, right in pairs:
            with self.subTest(pair=(left, right)):
                self.assertNotEqual(self.key(left), self.key(right))

    def test_empty_names_have_no_key(self):
        self.assertEqual(self.key(None), "")
        self.assertEqual(self.key("   "), "")

    def test_key_is_stable_and_non_empty_for_a_bare_title(self):
        self.assertEqual(self.key("Hoppo"), "hoppo")
        self.assertEqual(self.key("the Hoppo"), "hoppo")
        self.assertTrue(self.key("Mr"))


class AmbiguityPolicyTests(unittest.TestCase):
    """A shared identity key must never be resolved by "pick the oldest record"."""

    def setUp(self):
        from app.models.kg.person import Person
        from app.services.kg.kg_writer import kg_writer

        self.kg_writer = kg_writer
        self.person = Person
        self.sir = Person(id=1, display_name="Sir Edward Hughes")
        self.mr = Person(id=2, display_name="Mr. Edward Hughes")
        self.index = {"edward hughes": [self.sir, self.mr]}

    def find(self, name, index=None):
        return self.kg_writer._find_existing_person(
            None, name, self.index if index is None else index
        )

    def test_two_candidates_and_a_new_spelling_resolve_to_nothing(self):
        match, ambiguous = self.find("Edward Hughes Esq")
        self.assertIsNone(match, "picking one of them would be an identity guess")
        self.assertEqual([person.id for person in ambiguous], [1, 2])

    def test_an_exact_spelling_still_reuses_the_record_that_holds_it(self):
        match, ambiguous = self.find("Mr. Edward Hughes")
        self.assertIs(self.mr, match)
        self.assertEqual(ambiguous, [])

    def test_a_single_candidate_is_matched(self):
        match, ambiguous = self.find("Cap. Rogers", {"rogers": [self.sir]})
        self.assertIs(self.sir, match)
        self.assertEqual(ambiguous, [])

    def test_no_candidate_is_reported_as_no_candidate(self):
        match, ambiguous = self.find("Nobody At All", {})
        self.assertIsNone(match)
        self.assertEqual(ambiguous, [])

    def test_a_nameless_entry_yields_no_key(self):
        match, ambiguous = self.find("   ")
        self.assertIsNone(match)
        self.assertEqual(ambiguous, [])

    def test_a_merged_away_record_is_not_a_candidate(self):
        from app.models.kg.enums import IdentityStatus

        absorbed = self.person(
            id=3, display_name="Capt. Rogers", identity_status=IdentityStatus.REJECTED
        )
        match, ambiguous = self.find("Captain Rogers", {"rogers": [absorbed]})
        self.assertIsNone(match, "evidence must not be attached to a retired identity")
        self.assertEqual(ambiguous, [])


if __name__ == "__main__":
    unittest.main()
