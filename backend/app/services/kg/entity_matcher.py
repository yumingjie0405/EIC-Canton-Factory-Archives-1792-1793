"""Multi-dimensional explainable entity matching and contradiction detection engine."""
import difflib
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.kg.authority import ExternalAuthority
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship
from app.services.kg.name_normalizer import name_normalizer


@dataclass
class MatchEvaluation:
    """Detailed explainable breakdown of person-mention match scoring."""
    mention_id: int
    candidate_person_id: int
    name_score: float = 0.0
    temporal_score: float = 0.5
    spatial_score: float = 0.5
    role_score: float = 0.5
    context_score: float = 0.0
    authority_score: float = 0.0
    total_score: float = 0.0

    supporting_evidence: List[str] = field(default_factory=list)
    contradiction_flags: List[str] = field(default_factory=list)
    recommendation: str = "needs_review"  # 'confirm', 'needs_review', 'reject'


class EntityMatcher:
    """Computes interpretable multi-dimensional match scores between a mention and a candidate Person."""

    # Canton archival locations with high compatibility
    CANTON_LOCATIONS: Set[str] = {
        "canton", "whampoa", "macao", "banton", "danes island", "french island", "pearl river"
    }

    def evaluate_match(
        self,
        db: Session,
        mention: PersonMention,
        candidate: Person,
    ) -> MatchEvaluation:
        """
        Evaluate compatibility across Name, Time, Space, Role, Context, and External Authorities.
        Flags historical contradictions and outputs an interpretable MatchEvaluation object.
        """
        eval_result = MatchEvaluation(
            mention_id=mention.id,
            candidate_person_id=candidate.id,
        )

        supporting: List[str] = []
        contradictions: List[str] = []

        # ----------------------------------------------------
        # 1. Name & Variant Dimension
        # ----------------------------------------------------
        raw_text = mention.raw_text or ""
        norm_mention = mention.normalized_text or name_normalizer.normalize_for_matching(raw_text)
        cand_display_norm = name_normalizer.normalize_for_matching(candidate.display_name)

        name_sim = difflib.SequenceMatcher(None, norm_mention, cand_display_norm).ratio()
        if norm_mention == cand_display_norm:
            name_sim = 1.0
            supporting.append(f"Exact normalized name match: '{norm_mention}' == '{cand_display_norm}'")
        elif norm_mention in cand_display_norm or cand_display_norm in norm_mention:
            name_sim = max(name_sim, 0.90)
            supporting.append(f"Sub-string name match between '{norm_mention}' and '{cand_display_norm}'")

        # Check aliases
        if candidate.aliases:
            alias_list = []
            if isinstance(candidate.aliases, list):
                alias_list = [str(a) for a in candidate.aliases]
            elif isinstance(candidate.aliases, dict):
                alias_list = [str(v) for v in candidate.aliases.values()]

            for a in alias_list:
                a_norm = name_normalizer.normalize_for_matching(a)
                if norm_mention == a_norm:
                    name_sim = max(name_sim, 0.98)
                    supporting.append(f"Matched known person alias: '{a}'")
                    break

        eval_result.name_score = round(name_sim, 3)

        # ----------------------------------------------------
        # 2. Temporal Compatibility Dimension & Hard Conflict
        # ----------------------------------------------------
        mention_year = mention.temporal_year or (mention.temporal_date.year if mention.temporal_date else None)
        temp_score = 0.5  # Neutral when dates unrecorded

        if mention_year:
            # Check death bounds
            if candidate.death_year and mention_year > candidate.death_year:
                contradictions.append(
                    f"Temporal contradiction: mention year {mention_year} is after death year {candidate.death_year}"
                )
                temp_score = 0.0
            # Check birth bounds
            elif candidate.birth_year and mention_year < candidate.birth_year:
                contradictions.append(
                    f"Temporal contradiction: mention year {mention_year} is before birth year {candidate.birth_year}"
                )
                temp_score = 0.0
            # Check active period bounds
            elif candidate.active_start and candidate.active_end:
                start_yr = candidate.active_start.year
                end_yr = candidate.active_end.year
                if start_yr <= mention_year <= end_yr:
                    temp_score = 1.0
                    supporting.append(f"Mention year {mention_year} falls squarely within active period {start_yr}-{end_yr}")
                elif abs(mention_year - start_yr) <= 3 or abs(mention_year - end_yr) <= 3:
                    temp_score = 0.75
                    supporting.append(f"Mention year {mention_year} is close to active period {start_yr}-{end_yr}")
                else:
                    contradictions.append(
                        f"Temporal distance: mention year {mention_year} deviates from active period {start_yr}-{end_yr}"
                    )
                    temp_score = 0.2
            elif candidate.birth_year or candidate.death_year:
                temp_score = 0.8
                supporting.append(f"Mention year {mention_year} is compatible with lifespan")

        eval_result.temporal_score = round(temp_score, 3)

        # ----------------------------------------------------
        # 3. Spatial Compatibility Dimension
        # ----------------------------------------------------
        spatial_score = 0.5
        m_loc = (mention.spatial_location or "").strip().lower()
        c_loc = (candidate.base_location or "").strip().lower()

        if m_loc and c_loc:
            if m_loc == c_loc or m_loc in c_loc or c_loc in m_loc:
                spatial_score = 0.95
                supporting.append(f"Location match: '{m_loc}' aligns with '{c_loc}'")
            elif m_loc in self.CANTON_LOCATIONS and c_loc in self.CANTON_LOCATIONS:
                spatial_score = 0.85
                supporting.append(f"Both locations belong to Canton Factory trade sphere ('{m_loc}', '{c_loc}')")
            else:
                spatial_score = 0.3
                supporting.append(f"Location divergence: mention at '{m_loc}' vs candidate at '{c_loc}'")
        elif m_loc in self.CANTON_LOCATIONS or c_loc in self.CANTON_LOCATIONS:
            spatial_score = 0.7

        eval_result.spatial_score = round(spatial_score, 3)

        # ----------------------------------------------------
        # 4. Role & Organizational Identity Dimension
        # ----------------------------------------------------
        role_score = 0.5
        c_role = (candidate.primary_role or "").lower()
        ev_text = (mention.evidence_text or raw_text).lower()

        if c_role:
            if c_role in ev_text:
                role_score = 0.95
                supporting.append(f"Role title '{candidate.primary_role}' explicitly mentioned in context")
            elif any(token in ev_text for token in c_role.split()):
                role_score = 0.80
                supporting.append(f"Partial role match for '{candidate.primary_role}'")
            else:
                # Check known conflicting roles (e.g. Captain vs Mandarin)
                if ("captain" in c_role and "mandarin" in ev_text) or ("hoppo" in c_role and "supercargo" in ev_text):
                    contradictions.append(f"Role conflict: candidate is '{candidate.primary_role}', context suggests conflicting role")
                    role_score = 0.1

        eval_result.role_score = round(role_score, 3)

        # ----------------------------------------------------
        # 5. Relational Context & Co-occurrence Dimension
        # ----------------------------------------------------
        context_score = 0.0
        # Check if candidate has existing relationships connected to other mentions on the same block
        peer_mentions = (
            db.query(PersonMention)
            .filter(PersonMention.block_id == mention.block_id, PersonMention.id != mention.id)
            .all()
        )
        peer_person_ids = {pm.person_id for pm in peer_mentions if pm.person_id is not None}

        if peer_person_ids:
            connected_rel = (
                db.query(Relationship)
                .filter(
                    (Relationship.source_person_id == candidate.id) & (Relationship.target_person_id.in_(peer_person_ids)) |
                    (Relationship.target_person_id == candidate.id) & (Relationship.source_person_id.in_(peer_person_ids))
                )
                .first()
            )
            if connected_rel:
                context_score = 0.9
                supporting.append(f"Known historical relationship exists with co-occurring entity in block {mention.block_id}")
            else:
                context_score = 0.3

        eval_result.context_score = round(context_score, 3)

        # ----------------------------------------------------
        # 6. External Authority Support Dimension
        # ----------------------------------------------------
        auth_score = 0.0
        authorities = (
            db.query(ExternalAuthority)
            .filter(ExternalAuthority.person_id == candidate.id)
            .all()
        )
        if authorities:
            auth_sources = [a.authority_source.value for a in authorities]
            auth_score = min(1.0, 0.4 + len(authorities) * 0.2)
            supporting.append(f"Candidate backed by external authorities: {', '.join(auth_sources)}")

        eval_result.authority_score = round(auth_score, 3)

        # ----------------------------------------------------
        # Aggregate & Contradiction Resolution Strategy
        # ----------------------------------------------------
        eval_result.supporting_evidence = supporting
        eval_result.contradiction_flags = contradictions

        if contradictions:
            # Harsh penalty if any fatal contradiction exists
            total = (eval_result.name_score * 0.3) * (0.1 if "Temporal contradiction" in str(contradictions) else 0.5)
            eval_result.total_score = round(total, 3)
            eval_result.recommendation = "reject" if temp_score == 0.0 else "needs_review"
        else:
            # Weighted explainable fusion
            weights = {
                "name": 0.40,
                "temporal": 0.20,
                "spatial": 0.10,
                "role": 0.15,
                "context": 0.10,
                "authority": 0.05,
            }
            total = (
                eval_result.name_score * weights["name"] +
                eval_result.temporal_score * weights["temporal"] +
                eval_result.spatial_score * weights["spatial"] +
                eval_result.role_score * weights["role"] +
                eval_result.context_score * weights["context"] +
                eval_result.authority_score * weights["authority"]
            )
            eval_result.total_score = round(total, 3)

            if eval_result.total_score >= 0.88 and eval_result.name_score >= 0.90:
                eval_result.recommendation = "confirm"
            elif eval_result.total_score >= 0.55:
                eval_result.recommendation = "needs_review"
            else:
                eval_result.recommendation = "reject"

        return eval_result


entity_matcher = EntityMatcher()
