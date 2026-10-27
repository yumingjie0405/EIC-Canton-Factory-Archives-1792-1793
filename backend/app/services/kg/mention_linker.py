"""Deterministic mention → person linking (candidate stage only).

Why this module exists
----------------------
The extraction writer stored every ``person_mentions`` row with ``person_id=None``
("unresolved at Stage 2") and only created ``persons`` for relationship endpoints.
The graph therefore showed people that provably came from the archive, while the
person drawer reported *zero* mentions for all of them — the interface promised
provenance it could not deliver.

Linking is deliberately boring and conservative:

* the comparison key is :meth:`NameNormalizer.identity_key` (case, punctuation,
  hyphens, leading articles and rank abbreviations folded; titles removed), so
  ``the Hoppe``/``Hoppe`` and ``Cap. Rogers``/``Capt. Rogers`` are one referent;
* a mention is only linked when **exactly one** person carries that key — the moment
  two candidates share a key the mention stays unlinked, because choosing between
  them is an identity decision, not a lookup;
* the link is written as ``resolution_status=candidate`` with the existing review
  status untouched: extraction may *propose* a link, a human still confirms it;
* rejected mentions/persons are never revived, and already-linked mentions are never
  re-pointed.

Everything here is idempotent, so it can run after every block write and again as a
backfill over historical rows.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import logger
from app.models.kg.enums import IdentityStatus, ResolutionStatus
from app.models.kg.mention import PersonMention
from app.models.kg.person import Person
from app.services.kg.name_normalizer import name_normalizer


def person_keys(person: Person) -> Set[str]:
    """Every identity key a person can be matched by (display + canonical names)."""
    keys = set()
    for value in (person.display_name, person.canonical_name_en, person.canonical_name_zh):
        key = name_normalizer.identity_key(value)
        if key:
            keys.add(key)
    aliases = person.aliases
    if isinstance(aliases, list):
        alias_values: Iterable[object] = aliases
    elif isinstance(aliases, dict):
        alias_values = aliases.values()
    else:
        alias_values = ()
    for alias in alias_values:
        key = name_normalizer.identity_key(str(alias))
        if key:
            keys.add(key)
    return keys


def mention_key(mention: PersonMention) -> str:
    """Comparison key of a mention: the model's normalization when usable, else raw."""
    for value in (mention.normalized_text, mention.raw_text):
        key = name_normalizer.identity_key(value)
        if key:
            return key
    return ""


def identity_index(
    db: Session, person_ids: Optional[Sequence[int]] = None
) -> Dict[str, List[Person]]:
    """Map identity key -> candidate persons, for readers that need the rows."""
    query = db.query(Person).filter(Person.identity_status != IdentityStatus.REJECTED)
    if person_ids is not None:
        query = query.filter(Person.id.in_(list(person_ids)))
    index: Dict[str, List[Person]] = {}
    for person in query.all():
        for key in person_keys(person):
            bucket = index.setdefault(key, [])
            if all(other.id != person.id for other in bucket):
                bucket.append(person)
    return index


def _key_index(db: Session, person_ids: Optional[Sequence[int]] = None) -> Tuple[Dict[str, List[int]], int]:
    """Map identity key -> person ids, plus the number of ambiguous keys."""
    index = identity_index(db, person_ids)
    ambiguous = sum(1 for people in index.values() if len(people) > 1)
    return {key: [person.id for person in people] for key, people in index.items()}, ambiguous


def reconcile(
    db: Session,
    block_ids: Optional[Sequence[int]] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Link unlinked mentions to the single unambiguous person with the same name.

    ``block_ids`` restricts the work to specific blocks (the per-block hook);
    ``None`` reconciles every unlinked mention (the backfill).
    """
    index, ambiguous_keys = _key_index(db)

    query = db.query(PersonMention).filter(
        PersonMention.person_id.is_(None),
        PersonMention.resolution_status != ResolutionStatus.REJECTED,
    )
    if block_ids is not None:
        ids = list(block_ids)
        if not ids:
            return {
                "examined": 0,
                "linked": 0,
                "ambiguous": 0,
                "no_person": 0,
                "ambiguous_keys": ambiguous_keys,
            }
        query = query.filter(PersonMention.block_id.in_(ids))

    examined = linked = ambiguous = no_person = 0
    for mention in query.all():
        examined += 1
        key = mention_key(mention)
        candidates = index.get(key) or []
        if not candidates:
            no_person += 1
            continue
        if len(candidates) > 1:
            ambiguous += 1
            continue
        if dry_run:
            linked += 1
            continue
        mention.person_id = candidates[0]
        # A proposal, never an acceptance: the reviewer still has to confirm identity.
        if mention.resolution_status == ResolutionStatus.UNRESOLVED:
            mention.resolution_status = ResolutionStatus.CANDIDATE
        linked += 1

    if not dry_run and linked:
        db.flush()
        logger.info(
            "mention_linker: linked %d mentions (examined=%d ambiguous=%d no_person=%d)",
            linked,
            examined,
            ambiguous,
            no_person,
        )

    return {
        "examined": examined,
        "linked": linked,
        "ambiguous": ambiguous,
        "no_person": no_person,
        "ambiguous_keys": ambiguous_keys,
    }


def reconcile_block(db: Session, block_id: int) -> Dict[str, int]:
    """Per-block hook used right after a block's extraction is written."""
    return reconcile(db, block_ids=[block_id])


def unlinked_name_matches(
    db: Session,
    person: Person,
    block_ids: Sequence[int],
    limit: int,
    query=None,
) -> Tuple[int, List[object]]:
    """Mentions in scope that share this person's name but are still unlinked.

    Shown in the person drawer as "same name, not yet linked" so the reader is never
    told a person has no archival trace when the same name is in fact present.

    ``query`` lets the caller pass the shared column-level locator query (already
    filtered to the group and to ``person_id IS NULL``); the name comparison itself has
    to happen in Python because the comparison key is defined by the normalizer, not by
    a SQL expression. Rows are returned untouched so the caller can shape them.
    """
    keys = person_keys(person)
    if not keys or not block_ids:
        return 0, []

    if query is not None:
        rows = query.filter(PersonMention.block_id.in_(list(block_ids))).order_by(
            PersonMention.id.asc()
        ).all()
        matched = [row for row in rows if _row_key(row) in keys]
        return len(matched), matched[:limit]

    candidates = (
        db.query(PersonMention)
        .filter(
            PersonMention.person_id.is_(None),
            PersonMention.resolution_status != ResolutionStatus.REJECTED,
            PersonMention.block_id.in_(list(block_ids)),
        )
        .order_by(PersonMention.id.asc())
        .all()
    )
    matched = [mention for mention in candidates if mention_key(mention) in keys]
    return len(matched), matched[:limit]


def _row_key(row) -> str:
    """Comparison key of a column-level mention row (raw_text is column index 1)."""
    for value in (row[2], row[1]):  # normalized_text, raw_text
        key = name_normalizer.identity_key(value)
        if key:
            return key
    return ""


def duplicate_person_candidates(db: Session) -> List[Dict[str, object]]:
    """Groups of persons that share a normalized key (review input, never auto-merged).

    Merging two identities is a historical judgement, so this only *reports*: the
    reviewer decides via the existing entity-merge workflow.
    """
    index, _ = _key_index(db)
    persons = {person.id: person for person in db.query(Person).all()}
    rows: List[Dict[str, object]] = []
    for key, ids in sorted(index.items()):
        if len(ids) < 2:
            continue
        rows.append(
            {
                "key": key,
                "person_ids": sorted(ids),
                "display_names": [persons[pid].display_name for pid in sorted(ids) if pid in persons],
                "identity_statuses": [
                    str(getattr(persons[pid].identity_status, "value", persons[pid].identity_status))
                    for pid in sorted(ids)
                    if pid in persons
                ],
            }
        )
    return rows
