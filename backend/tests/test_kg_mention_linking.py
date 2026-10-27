"""Unit tests: name normalization and deterministic mention linking rules.

Two reported symptoms are covered here, both pure (no database):

* ``the Hoppe`` and ``Hoppe`` were stored as two different people;
* every person in the graph reported "0 mentions" because nothing was ever linked.

The comparison key used by the linker must therefore ignore honorifics *and* leading
articles, while the linker itself must refuse to guess between two same-named people.
"""
import unittest

from app.models.kg.enums import IdentityStatus, ResolutionStatus, ReviewStatusKG
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.services.kg import mention_linker
from app.services.kg.name_normalizer import name_normalizer


class NameNormalizationTests(unittest.TestCase):
    def test_leading_article_is_not_part_of_the_identity(self):
        self.assertEqual(name_normalizer.normalize_for_matching("the Hoppe"), "hoppe")
        self.assertEqual(name_normalizer.normalize_for_matching("Hoppe"), "hoppe")
        self.assertEqual(
            name_normalizer.normalize_for_matching("the Hong Merchants"), "hong merchants"
        )

    def test_titles_are_stripped_before_comparison(self):
        self.assertEqual(
            name_normalizer.normalize_for_matching("Mr. Henry Browne"), "henry browne"
        )
        self.assertEqual(
            name_normalizer.normalize_for_matching("Capt. Henry Churchill"),
            name_normalizer.normalize_for_matching("Henry Churchill"),
        )

    def test_a_lone_article_is_not_stripped_to_nothing(self):
        # "the" on its own must survive; otherwise every article would collapse to "".
        self.assertEqual(name_normalizer.normalize_for_matching("the"), "the")

    def test_similar_but_different_names_stay_different(self):
        self.assertNotEqual(
            name_normalizer.normalize_for_matching("Hoppe"),
            name_normalizer.normalize_for_matching("Hopps"),
        )

    def test_raw_text_is_never_modified(self):
        raw = "the Hoppe"
        name_normalizer.normalize_for_matching(raw)
        self.assertEqual(raw, "the Hoppe")


class MentionLinkingKeyTests(unittest.TestCase):
    @staticmethod
    def _person(name: str) -> Person:
        person = Person(display_name=name, canonical_name_en=name)
        person.identity_status = IdentityStatus.PROVISIONAL
        return person

    @staticmethod
    def _mention(raw: str, normalized: str = None) -> PersonMention:
        mention = PersonMention(
            block_id=1,
            raw_text=raw,
            normalized_text=normalized or raw,
            resolution_status=ResolutionStatus.UNRESOLVED,
            review_status=ReviewStatusKG.UNREVIEWED,
        )
        return mention

    def test_person_keys_cover_display_and_canonical_names(self):
        person = self._person("the Hoppe")
        person.canonical_name_en = "Hoppo"
        keys = mention_linker.person_keys(person)
        self.assertIn("hoppe", keys)
        self.assertIn("hoppo", keys)

    def test_person_keys_include_aliases(self):
        person = self._person("Henry Browne")
        person.aliases = ["H. Browne", "President Browne"]
        keys = mention_linker.person_keys(person)
        self.assertIn("h browne", keys)
        # "President" is a title prefix, so the alias reduces to the surname: still a
        # usable key, just a deliberately loose one.
        self.assertIn("browne", keys)

    def test_mention_key_prefers_model_normalization(self):
        mention = self._mention("the Hoppe", "Hoppo")
        self.assertEqual(mention_linker.mention_key(mention), "hoppo")

    def test_mention_key_falls_back_to_raw_text(self):
        mention = self._mention("the Hoppe", "")
        self.assertEqual(mention_linker.mention_key(mention), "hoppe")


if __name__ == "__main__":
    unittest.main()
