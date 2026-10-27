"""Entity operation audit model for recording merges, splits, and disambiguation decisions."""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.kg.person import Person


class EntityOperationAudit(Base):
    """Immutable audit trail for entity resolution, manual review, merge and split actions."""

    __tablename__ = "entity_operations_audit"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    operation_type = Column(String(50), nullable=False, index=True)
    # Types: 'merge', 'split', 'confirm_link', 'reject_link', 'defer_link'

    primary_person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    secondary_person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Detailed snapshot of modified IDs, pre-operation mappings, and confidence scores
    snapshot_details = Column(JSON, nullable=False, default=dict)

    reason = Column(Text, nullable=True)
    operator = Column(String(100), default="system", nullable=False)
    is_reverted = Column(Boolean, default=False, nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    primary_person: "Person" = relationship(
        "Person",
        foreign_keys=[primary_person_id],
        backref="audit_primary_operations",
    )
    secondary_person: Optional["Person"] = relationship(
        "Person",
        foreign_keys=[secondary_person_id],
        backref="audit_secondary_operations",
    )

    def __repr__(self) -> str:
        return (
            f"<EntityOperationAudit(id={self.id}, type='{self.operation_type}', "
            f"primary_person_id={self.primary_person_id}, reverted={self.is_reverted})>"
        )
