"""Repository for persisted research records."""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.research import ResearchMode, ResearchSession


class ResearchRepository:
    """Persistence operations for :class:`ResearchSession`.

    The repository never creates an Engine and never commits anything other
    than its own model, so a failing write surfaces to the caller instead of
    being mistaken for a saved record.
    """

    def __init__(self, db: Session):
        self.db = db

    def add(self, record: ResearchSession) -> ResearchSession:
        """Persist a record and return it with its generated primary key."""
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get_by_id(self, record_id: int) -> Optional[ResearchSession]:
        return self.db.query(ResearchSession).filter(ResearchSession.id == record_id).first()

    def list(
        self,
        offset: int = 0,
        limit: int = 20,
        mode: Optional[ResearchMode] = None,
        group_id: Optional[int] = None,
    ) -> List[ResearchSession]:
        query = self.db.query(ResearchSession)
        if mode is not None:
            query = query.filter(ResearchSession.mode == mode)
        if group_id is not None:
            query = query.filter(ResearchSession.group_id == group_id)
        return (
            query.order_by(ResearchSession.created_at.desc(), ResearchSession.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count(
        self, mode: Optional[ResearchMode] = None, group_id: Optional[int] = None
    ) -> int:
        query = self.db.query(ResearchSession)
        if mode is not None:
            query = query.filter(ResearchSession.mode == mode)
        if group_id is not None:
            query = query.filter(ResearchSession.group_id == group_id)
        return query.count()
