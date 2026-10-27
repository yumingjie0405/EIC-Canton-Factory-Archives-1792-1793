"""Candidate person generation engine for mention disambiguation."""
import difflib
from typing import List, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.services.kg.name_normalizer import name_normalizer


class CandidateGenerator:
    """Generates a bounded set of plausible canonical Person candidates for a PersonMention."""

    @staticmethod
    def _is_temporally_incompatible(mention: PersonMention, person: Person) -> bool:
        """
        Hard constraint check: Returns True if mention date is definitively outside person's lifetime.
        Prevents generation of historically impossible candidates (e.g. deceased before archive date).
        """
        mention_year = mention.temporal_year or (mention.temporal_date.year if mention.temporal_date else None)
        if not mention_year:
            return False

        # Check birth and death bounds
        if person.death_year and mention_year > person.death_year + 1:
            return True
        if person.birth_year and mention_year < person.birth_year:
            return True

        # Check known active year bounds (with 5-year historical margin)
        if person.active_end and mention_year > person.active_end.year + 5:
            return True
        if person.active_start and mention_year < person.active_start.year - 5:
            return True

        return False

    def generate_candidates(
        self,
        db: Session,
        mention: PersonMention,
        limit: int = 10,
        similarity_threshold: float = 0.55,
    ) -> List[Tuple[Person, float]]:
        """
        Retrieve potential candidate Person records matching mention by name, aliases, or role.
        Returns sorted list of (Person, preliminary_similarity_score).
        """
        raw_text = mention.raw_text or ""
        norm_mention = mention.normalized_text or name_normalizer.normalize_for_matching(raw_text)

        all_persons = db.query(Person).all()
        candidates_scored: List[Tuple[Person, float]] = []

        for p in all_persons:
            # 1. Hard temporal exclusion
            if self._is_temporally_incompatible(mention, p):
                continue

            # 2. Name & Alias similarity scoring
            scores = []

            # Compare against display_name
            p_display_norm = name_normalizer.normalize_for_matching(p.display_name)
            if p_display_norm:
                sim = difflib.SequenceMatcher(None, norm_mention, p_display_norm).ratio()
                if norm_mention == p_display_norm:
                    sim = 1.0
                elif norm_mention in p_display_norm or p_display_norm in norm_mention:
                    sim = max(sim, 0.85)
                scores.append(sim)

            # Compare against canonical names
            if p.canonical_name_en:
                p_en_norm = name_normalizer.normalize_for_matching(p.canonical_name_en)
                sim_en = difflib.SequenceMatcher(None, norm_mention, p_en_norm).ratio()
                scores.append(sim_en)

            if p.canonical_name_zh:
                if raw_text.strip() == p.canonical_name_zh.strip():
                    scores.append(1.0)

            # Compare against aliases
            if p.aliases:
                alias_list: List[str] = []
                if isinstance(p.aliases, list):
                    alias_list = [str(a) for a in p.aliases]
                elif isinstance(p.aliases, dict):
                    alias_list = [str(v) for v in p.aliases.values()]

                for a in alias_list:
                    a_norm = name_normalizer.normalize_for_matching(a)
                    if norm_mention == a_norm:
                        scores.append(1.0)
                    elif norm_mention in a_norm or a_norm in norm_mention:
                        scores.append(0.88)
                    else:
                        scores.append(difflib.SequenceMatcher(None, norm_mention, a_norm).ratio())

            max_score = max(scores) if scores else 0.0

            # Boost if primary_role matches mention context
            if p.primary_role and norm_mention:
                role_norm = p.primary_role.lower()
                if role_norm in raw_text.lower():
                    max_score = min(1.0, max_score + 0.1)

            if max_score >= similarity_threshold:
                candidates_scored.append((p, round(max_score, 3)))

        # Sort by preliminary score descending
        candidates_scored.sort(key=lambda x: x[1], reverse=True)
        return candidates_scored[:limit]


candidate_generator = CandidateGenerator()
