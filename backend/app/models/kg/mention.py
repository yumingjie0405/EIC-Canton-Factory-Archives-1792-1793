"""PersonMention model representing an archived reference to a historical person."""
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.kg.enums import MentionType, ResolutionStatus, ReviewStatusKG

if TYPE_CHECKING:
    from app.models.block import Block
    from app.models.kg.person import Person
    from app.models.kg.relationship import RelationshipEvidence


class PersonMention(Base):
    """Archival mention of a person tied directly to a transcribed OCR block."""

    __tablename__ = "person_mentions"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    # Foreign Keys
    block_id = Column(
        Integer,
        ForeignKey("blocks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Text representations
    raw_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=True, index=True)
    mention_type = Column(
        Enum(
            MentionType,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=50,
        ),
        default=MentionType.PERSON_NAME,
        nullable=False,
    )

    # Spatio-Temporal Attribution
    temporal_date = Column(Date, nullable=True, index=True)
    temporal_year = Column(SmallInteger, nullable=True)
    spatial_location = Column(String(255), nullable=True)

    # Disambiguation & Confidence
    confidence = Column(Float, nullable=True)
    resolution_status = Column(
        Enum(
            ResolutionStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=30,
        ),
        default=ResolutionStatus.UNRESOLVED,
        nullable=False,
        index=True,
    )
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

    candidate_person_ids = Column(JSON, nullable=True)  # List[int]
    evidence_text = Column(Text, nullable=True)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    #: Role wording exactly as the extractor reported it for *this* occurrence, plus its
    #: provenance. Keeping it per mention (block-scoped, run-scoped) means a role can be
    #: traced back to the sentence that produced it instead of existing only as a bare
    #: string on the person row.
    role_text = Column(String(120), nullable=True)
    role_source = Column(String(24), nullable=True, index=True)

    # Provenance: which run wrote this row, and what kind of producer that run was.
    # Nullable because rows written before the ledger existed cannot be attributed.
    extraction_run_id = Column(
        Integer,
        ForeignKey("kg_extraction_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_kind = Column(String(24), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    block: "Block" = relationship("Block")
    person: Optional["Person"] = relationship("Person", back_populates="mentions")
    supported_evidences: List["RelationshipEvidence"] = relationship(
        "RelationshipEvidence",
        back_populates="mention",
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="chk_mention_confidence_range",
        ),
        CheckConstraint(
            "length(trim(raw_text)) > 0",
            name="chk_mention_raw_text_nonempty",
        ),
        Index(
            "ix_person_mentions_status",
            "resolution_status",
            "review_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<PersonMention(id={self.id}, block_id={self.block_id}, raw_text='{self.raw_text}')>"
