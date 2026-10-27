"""Server-side research scope (document group) resolution and enforcement.

Contract
--------
* ``group_id=None``  -> unscoped research (legacy behaviour, explicit choice).
* ``group_id=<id>``  -> the scope is loaded **on the server**; an unknown group
  raises :class:`GroupNotFound` (HTTP 404) and an empty group yields zero
  evidence. There is no fallback to a global search.
* Every source handed to the answer layer must carry a ``document_id`` that is
  inside the scope; unverifiable sources are dropped instead of being presented
  as group evidence.
"""
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.models.research_group import ResearchGroup, ResearchGroupDocument


class GroupScopeError(Exception):
    """Base class for scope problems (mapped to an HTTP error by the routers)."""


class GroupNotFound(GroupScopeError):
    """The requested group does not exist."""


@dataclass(frozen=True)
class GroupScope:
    """An immutable snapshot of the documents a run may search."""

    group_id: int
    group_name: str
    document_ids: frozenset

    @property
    def is_empty(self) -> bool:
        return not self.document_ids

    def allows(self, document_id: Optional[int]) -> bool:
        if document_id is None:
            return False
        try:
            return int(document_id) in self.document_ids
        except (TypeError, ValueError):
            return False

    def describe(self) -> str:
        return "研究组 #%d《%s》（%d 篇文档）" % (
            self.group_id,
            self.group_name,
            len(self.document_ids),
        )


def load_group_scope(db: Session, group_id: int) -> GroupScope:
    """Load the scope for *group_id*, raising when the group does not exist."""
    group = db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
    if group is None:
        raise GroupNotFound("文档组 #%s 不存在或无权访问。" % group_id)
    document_ids = {
        row[0]
        for row in db.query(ResearchGroupDocument.document_id)
        .filter(ResearchGroupDocument.group_id == group_id)
        .all()
    }
    return GroupScope(
        group_id=group.id,
        group_name=group.name,
        document_ids=frozenset(document_ids),
    )


def optional_group_scope(db: Session, group_id: Optional[int]) -> Optional[GroupScope]:
    """Return ``None`` for unscoped research, otherwise the validated scope."""
    if group_id is None:
        return None
    return load_group_scope(db, group_id)


def restrict_document_ids(
    allowed: Optional[Iterable[int]],
    scope: Optional[GroupScope],
) -> Optional[Set[int]]:
    """Intersect an existing document-id filter with the group scope.

    ``None`` means "no filter". An empty set means "nothing is allowed" and must
    be honoured as an empty result, never as "no filter".
    """
    if scope is None:
        return None if allowed is None else set(allowed)
    scoped = set(scope.document_ids)
    if allowed is None:
        return scoped
    return scoped.intersection(set(allowed))


def filter_sources_by_document_ids(
    sources: Sequence[Any],
    allowed_document_ids: Optional[Set[int]],
) -> Tuple[list, list]:
    """Split sources by an allowed document-id set (``None`` = no restriction)."""
    if allowed_document_ids is None:
        return list(sources), []
    kept, dropped = [], []
    for source in sources:
        document_id = getattr(source, "document_id", None)
        if document_id is None and isinstance(source, dict):
            document_id = source.get("document_id")
        try:
            allowed = document_id is not None and int(document_id) in allowed_document_ids
        except (TypeError, ValueError):
            allowed = False
        (kept if allowed else dropped).append(source)
    return kept, dropped


def filter_sources_in_scope(
    sources: Sequence[Any],
    scope: Optional[GroupScope],
) -> Tuple[list, list]:
    """Split sources into (kept, dropped) according to the scope.

    A source without a verifiable ``document_id`` is dropped when a scope is
    active: it cannot be proven to belong to the group.
    """
    if scope is None:
        return list(sources), []
    return filter_sources_by_document_ids(sources, set(scope.document_ids))
