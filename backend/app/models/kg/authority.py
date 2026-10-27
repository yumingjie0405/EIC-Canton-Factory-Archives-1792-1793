"""ExternalAuthority model for linking historical persons to external authorities and sources."""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.kg.enums import AuthoritySource, ClaimStatus, ReviewStatusKG

if TYPE_CHECKING:
    from app.models.kg.person import Person


class ExternalAuthority(Base):
    """External authority and historiographical reference link for a person."""

    __tablename__ = "external_authorities"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    authority_source = Column(
        Enum(
            AuthoritySource,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=100,
        ),
        nullable=False,
        index=True,
    )
    external_id = Column(String(255), nullable=True, index=True)
    external_url = Column(Text, nullable=True)
    record_title = Column(String(500), nullable=True)
    historical_notes = Column(Text, nullable=True)

    match_confidence = Column(Float, nullable=True)
    review_status = Column(
        Enum(
            ReviewStatusKG,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=30,
        ),
        default=ReviewStatusKG.UNREVIEWED,
        nullable=False,
        index=True,
    )
    claim_status = Column(
        Enum(
            ClaimStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=30,
        ),
        default=ClaimStatus.ACCEPTED,
        nullable=False,
        index=True,
    )


    accessed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    person: "Person" = relationship("Person", back_populates="external_authorities")

    __table_args__ = (
        CheckConstraint(
            "match_confidence IS NULL OR (match_confidence >= 0.0 AND match_confidence <= 1.0)",
            name="chk_authority_confidence_range",
        ),
        Index(
            "ix_external_authorities_source_id",
            "authority_source",
            "external_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<ExternalAuthority(id={self.id}, person_id={self.person_id}, source='{self.authority_source}', ext_id='{self.external_id}')>"
