"""Historical person name normalization and variant management."""
import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple


# Known archival honorifics, titles, and prefixes/suffixes in 18th-century EIC Canton archives
TITLE_PREFIXES: Tuple[str, ...] = (
    "mr.", "mr", "mister",
    "mrs.", "mrs",
    "messrs.", "messrs",
    "capt.", "capt", "captain",
    "commander",
    "supercargo", "supra cargo",
    "president",
    "lord",
    "sir",
    "right hon.", "right honble", "hon.", "honble",
    "viceroy",
    "hoppo", "the hoppo",
    "governor",
    "general",
    "major",
    "colonel", "col.",
    "admiral",
    "dr.", "doctor",
)

TITLE_SUFFIXES: Tuple[str, ...] = (
    "jun.", "jun", "junior",
    "sen.", "sen", "senior",
    "esq.", "esq", "esquire",
)

#: Leading articles that carry no identity information in archival shorthand.
#: "the Hoppe" and "Hoppe" are the same referent; without this the same merchant was
#: stored twice and the graph showed two separate nodes.
LEADING_ARTICLES: Tuple[str, ...] = ("the", "ye", "a", "an")

#: Rank abbreviations that all render the same rank in this corpus. ``Cap.``,
#: ``Caps.``, ``Cap!`` and ``Cap'l`` are OCR/period variants of ``Capt.``; without
#: expansion one captain was stored as four different graph nodes.
ABBREVIATION_EXPANSIONS: Dict[str, str] = {
    "cap": "captain",
    "caps": "captain",
    "capt": "captain",
    "captn": "captain",
    "capn": "captain",
    "cap'l": "captain",
    "capt'l": "captain",
    "lieut": "lieutenant",
    "genl": "general",
    "col": "colonel",
}


class NameNormalizer:
    """Normalizes archival person names for comparison while preserving historical raw forms."""

    @staticmethod
    def strip_accents_and_normalize_unicode(text: str) -> str:
        """Normalize Unicode characters into NFKC canonical form."""
        if not text:
            return ""
        return unicodedata.normalize("NFKC", text.strip())

    @classmethod
    def normalize_for_matching(cls, raw_name: Optional[str]) -> str:
        """
        Produce a canonical lowercase comparison string.
        Strips honorifics/titles, normalizes whitespace and punctuation differences.
        NEVER overwrites archival raw text.
        """
        if not raw_name:
            return ""

        s = cls.strip_accents_and_normalize_unicode(raw_name).lower()

        # Remove common punctuation while keeping hyphens and spaces
        s = re.sub(r"[\.,;:!\?\"\'\(\)\[\]]", " ", s)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()

        # Strip prefixes
        tokens = s.split(" ")
        changed = True
        while changed and tokens:
            changed = False
            for p in TITLE_PREFIXES:
                p_tokens = p.split(" ")
                if len(tokens) >= len(p_tokens) and tokens[:len(p_tokens)] == p_tokens:
                    tokens = tokens[len(p_tokens):]
                    changed = True
                    break

        # Strip a bare leading article ("the Hoppe" -> "hoppe") once titles are gone.
        while len(tokens) > 1 and tokens[0] in LEADING_ARTICLES:
            tokens = tokens[1:]

        # Strip suffixes
        changed = True
        while changed and tokens:
            changed = False
            for suf in TITLE_SUFFIXES:
                suf_tokens = suf.split(" ")
                if len(tokens) >= len(suf_tokens) and tokens[-len(suf_tokens):] == suf_tokens:
                    tokens = tokens[:-len(suf_tokens)]
                    changed = True
                    break

        normalized = " ".join(tokens).strip()
        return normalized if normalized else raw_name.strip().lower()

    @classmethod
    def identity_key(cls, raw_name: Optional[str]) -> str:
        """Deterministic identity key used to decide *who* a name refers to.

        Both the extraction writer (when it decides whether to create a new person) and
        the mention linker compare names through this key, so ``the Hoppe``/``Hoppe``
        and ``Cap. Rogers``/``Capt. Rogers`` can no longer become separate nodes.

        Only differences that carry no identity information are erased: case,
        punctuation, hyphens, leading articles, rank abbreviations and honorifics.
        Genuinely different spellings (``Chowqua`` vs ``Chowzua``) keep different keys:
        deciding those are one person is an identity judgement for human review, never
        a similarity threshold applied by code.
        """
        if not raw_name:
            return ""
        s = cls.strip_accents_and_normalize_unicode(raw_name).lower()
        s = s.replace("-", " ").replace("_", " ")
        s = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015]", " ", s)
        tokens = []
        for piece in s.split():
            token = piece.strip(",;:!?\"()[]")
            token = token.replace(".", "")
            tokens.append(ABBREVIATION_EXPANSIONS.get(token, token))
        # Leading articles are pure noise. Stripping them here (not only inside
        # normalize_for_matching) also fixes names that are *only* a title: a bare
        # title makes the matcher fall back to its input, and "the Hoppo" would then
        # keep its article while "Hoppo" would not.
        while len(tokens) > 1 and tokens[0] in LEADING_ARTICLES:
            tokens = tokens[1:]
        # normalize_for_matching strips titles/articles/suffixes and falls back to the
        # punctuation-free form when a name is *only* a title ("Mr" / "Mr." -> "mr").
        return cls.normalize_for_matching(" ".join(tokens))

    @classmethod
    def clean_display_name(cls, raw_name: str) -> str:
        """
        Produce a clean canonical capitalized display name suitable for Person.display_name.
        Retains original case capitalization where appropriate, stripping redundant titles.
        """
        if not raw_name:
            return ""

        s = cls.strip_accents_and_normalize_unicode(raw_name)
        # Check title removal in case-insensitive way
        comp = s.lower()
        for p in TITLE_PREFIXES:
            pattern = rf"^{re.escape(p)}\s+"
            match = re.search(pattern, comp)
            if match:
                s = s[match.end():]
                comp = s.lower()

        for suf in TITLE_SUFFIXES:
            pattern = rf"\s+{re.escape(suf)}$"
            match = re.search(pattern, comp)
            if match:
                s = s[:match.start()]
                comp = s.lower()

        cleaned = re.sub(r"\s+", " ", s).strip(" ,.-")
        return cleaned if cleaned else raw_name.strip()

    @classmethod
    def extract_variants(cls, raw_name: str, provenance: str = "archive_text") -> List[Dict[str, str]]:
        """
        Extract recognizable variants from raw archival name with provenance tracking.
        """
        if not raw_name or not raw_name.strip():
            return []

        raw = raw_name.strip()
        matching_form = cls.normalize_for_matching(raw)
        display_clean = cls.clean_display_name(raw)

        variants = [
            {"form": raw, "variant_type": "exact", "provenance": provenance},
        ]

        if display_clean and display_clean.lower() != raw.lower():
            variants.append({
                "form": display_clean,
                "variant_type": "title_stripped",
                "provenance": provenance,
            })

        if matching_form and matching_form != raw.lower():
            variants.append({
                "form": matching_form,
                "variant_type": "normalized_matching",
                "provenance": provenance,
            })

        # Check spacing variations for Chinese romanized merchant names (e.g., "How Qua" vs "Howqua")
        no_space_form = re.sub(r"\s+", "", matching_form)
        if no_space_form != matching_form:
            variants.append({
                "form": no_space_form,
                "variant_type": "collapsed_spacing",
                "provenance": provenance,
            })

        return variants


name_normalizer = NameNormalizer()
