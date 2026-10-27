"""External authority linking and historical historiographical reference integration."""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.kg.authority import ExternalAuthority
from app.models.kg.enums import AuthoritySource, ClaimStatus, ReviewStatusKG
from app.models.kg.person import Person


# Historical prior dictionary for 18th-century Canton Factory (1792-1793) figures
HISTORICAL_CANTON_PRIORS: List[Dict] = [
    {
        "canonical_name_en": "Wu Bingjian",
        "canonical_name_zh": "伍秉鉴",
        "display_name": "Howqua (Wu Bingjian)",
        "aliases": ["Howqua", "How Qua", "Wu Ping-chien", "怡和行伍浩官"],
        "primary_role": "Security Merchant (Hong Merchant)",
        "organization": "E-wo Hong (怡和行)",
        "birth_year": 1769,
        "death_year": 1843,
        "base_location": "Canton",
        "authorities": [
            {
                "source": AuthoritySource.LIANG_CANTON,
                "external_id": "Liang-Howqua-02",
                "record_title": "梁嘉彬《广东十三行考》怡和行伍氏考",
                "historical_notes": "伍秉鉴，又名浩官二世，接替其兄伍秉钧任怡和行行主。",
            },
            {
                "source": AuthoritySource.CBDB,
                "external_id": "CBDB-0062489",
                "record_title": "China Biographical Database: Wu Bingjian",
            }
        ]
    },
    {
        "canonical_name_en": "Wu Guoying",
        "canonical_name_zh": "伍国莹",
        "display_name": "Howqua I (Wu Guoying)",
        "aliases": ["Howqua I", "Wu Kuo-ying", "伍浩官一世"],
        "primary_role": "Security Merchant (Hong Merchant)",
        "organization": "E-wo Hong (怡和行)",
        "birth_year": 1731,
        "death_year": 1810,
        "base_location": "Canton",
        "authorities": [
            {
                "source": AuthoritySource.LIANG_CANTON,
                "external_id": "Liang-Howqua-01",
                "record_title": "梁嘉彬《广东十三行考》怡和行创设者考",
                "historical_notes": "伍国莹为初代浩官，后由其子秉钧、秉鉴相继经营。",
            }
        ]
    },
    {
        "canonical_name_en": "Henry Browne",
        "canonical_name_zh": "亨利·布朗",
        "display_name": "Henry Browne",
        "aliases": ["Mr. Browne", "Supercargo Browne", "President Browne", "H. Browne"],
        "primary_role": "Supercargo (President of Select Committee)",
        "organization": "East India Company Canton Factory",
        "birth_year": 1754,
        "death_year": 1812,
        "base_location": "Canton",
        "authorities": [
            {
                "source": AuthoritySource.IOR,
                "external_id": "IOR-G-12-100",
                "record_title": "IOR/G/12/100: Canton Consultations 1792-1793",
                "historical_notes": "President of the Select Committee at Canton during Lord Macartney's embassy.",
            }
        ]
    },
    {
        "canonical_name_en": "George Macartney",
        "canonical_name_zh": "乔治·马戛尔尼",
        "display_name": "George Lord Macartney",
        "aliases": ["Lord Macartney", "Ambassador Macartney", "马戛尔尼"],
        "primary_role": "Ambassador Extraordinary and Plenipotentiary",
        "organization": "British Diplomatic Mission to China",
        "birth_year": 1737,
        "death_year": 1806,
        "base_location": "Peking / Canton",
        "authorities": [
            {
                "source": AuthoritySource.IOR,
                "external_id": "IOR-G-12-91",
                "record_title": "British Library IOR: Macartney Embassy Papers",
            },
            {
                "source": AuthoritySource.WIKIDATA,
                "external_id": "Q335084",
                "record_title": "Wikidata: George Macartney, 1st Earl Macartney",
            }
        ]
    }
]


class AuthorityLinker:
    """Manages external authority record links, academic dispute citations, and historical priors."""

    def link_authority(
        self,
        db: Session,
        person_id: int,
        source: AuthoritySource,
        external_id: Optional[str] = None,
        record_title: Optional[str] = None,
        external_url: Optional[str] = None,
        historical_notes: Optional[str] = None,
        claim_status: ClaimStatus = ClaimStatus.ACCEPTED,
        review_status: ReviewStatusKG = ReviewStatusKG.REVIEWED,
        confidence: float = 0.95,
    ) -> ExternalAuthority:
        """Create or update external authority citation for a canonical Person."""
        existing = (
            db.query(ExternalAuthority)
            .filter_by(person_id=person_id, authority_source=source, external_id=external_id)
            .first()
        )
        if existing:
            existing.record_title = record_title or existing.record_title
            existing.external_url = external_url or existing.external_url
            existing.historical_notes = historical_notes or existing.historical_notes
            existing.claim_status = claim_status
            existing.review_status = review_status
            existing.match_confidence = confidence
            existing.accessed_at = datetime.utcnow()
            db.flush()
            return existing

        new_auth = ExternalAuthority(
            person_id=person_id,
            authority_source=source,
            external_id=external_id,
            external_url=external_url,
            record_title=record_title,
            historical_notes=historical_notes,
            claim_status=claim_status,
            review_status=review_status,
            match_confidence=confidence,
            accessed_at=datetime.utcnow(),
        )
        db.add(new_auth)
        db.flush()
        return new_auth

    def seed_historical_priors(self, db: Session) -> List[Person]:
        """Seed known historical figures with external authorities into database if not present."""
        created_persons = []
        for item in HISTORICAL_CANTON_PRIORS:
            existing = (
                db.query(Person)
                .filter_by(display_name=item["display_name"])
                .first()
            )
            if not existing:
                p = Person(
                    canonical_name_en=item["canonical_name_en"],
                    canonical_name_zh=item["canonical_name_zh"],
                    display_name=item["display_name"],
                    aliases=item["aliases"],
                    primary_role=item["primary_role"],
                    organization=item["organization"],
                    birth_year=item.get("birth_year"),
                    death_year=item.get("death_year"),
                    base_location=item.get("base_location"),
                )
                db.add(p)
                db.flush()
                existing = p
                created_persons.append(p)

            # Link authorities
            for auth in item.get("authorities", []):
                self.link_authority(
                    db=db,
                    person_id=existing.id,
                    source=auth["source"],
                    external_id=auth.get("external_id"),
                    record_title=auth.get("record_title"),
                    historical_notes=auth.get("historical_notes"),
                )

        db.commit()
        return created_persons


authority_linker = AuthorityLinker()
