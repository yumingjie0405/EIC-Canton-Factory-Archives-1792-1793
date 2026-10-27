"""Relationship and RelationshipEvidence models."""
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
    String,
    Text,
)
from sqlalchemy.orm import relationship as orm_relationship

from app.database import Base
from app.models.kg.enums import (
    ClaimStatus,
    Directionality,
    EvidenceRole,
    RelationType,
    ReviewStatusKG,
)

if TYPE_CHECKING:
    from app.models.block import Block
    from app.models.kg.mention import PersonMention
    from app.models.kg.person import Person


class Relationship(Base):
    """Historical relationship between two persons."""

    __tablename__ = "relationships"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    # Foreign Keys
    source_person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Semantics
    relation_type = Column(
        Enum(
            RelationType,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=80,
        ),
        default=RelationType.OTHER,
        nullable=False,
        index=True,
    )
    relation_label = Column(String(255), nullable=True)
    directionality = Column(
        Enum(
            Directionality,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=20,
        ),
        default=Directionality.DIRECTED,
        nullable=False,
    )
    weight = Column(Float, nullable=True)

    # Spatio-Temporal Scope
    valid_from = Column(Date, nullable=True)
    valid_to = Column(Date, nullable=True)
    time_description = Column(String(255), nullable=True)
    spatial_location = Column(String(255), nullable=True)

    # Evidence & Epistemology
    evidence_summary = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
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
        default=ClaimStatus.ASSERTED,
        nullable=False,
        index=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


    # Relationships
    source_person: "Person" = orm_relationship(
        "Person",
        foreign_keys=[source_person_id],
        back_populates="out_relationships",
    )
    target_person: "Person" = orm_relationship(
        "Person",
        foreign_keys=[target_person_id],
        back_populates="in_relationships",
    )
    evidences: List["RelationshipEvidence"] = orm_relationship(
        "RelationshipEvidence",
        back_populates="relationship",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="chk_relationship_dates",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="chk_relationship_confidence_range",
        ),
        CheckConstraint(
            "weight IS NULL OR weight >= 0.0",
            name="chk_relationship_weight_positive",
        ),
        Index("ix_relationships_temporal", "valid_from", "valid_to"),
    )

    def __repr__(self) -> str:
        return f"<Relationship(id={self.id}, source={self.source_person_id}, target={self.target_person_id}, type='{self.relation_type}')>"


class RelationshipEvidence(Base):
    """Archival evidence link grounding a relationship in mentions and blocks."""

    __tablename__ = "relationship_evidences"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    relationship_id = Column(
        Integer,
        ForeignKey("relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_mention_id = Column(
        Integer,
        ForeignKey("person_mentions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    block_id = Column(
        Integer,
        ForeignKey("blocks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    evidence_role = Column(
        Enum(
            EvidenceRole,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=30,
        ),
        default=EvidenceRole.SUPPORTS,
        nullable=False,
    )

    quote_text = Column(Text, nullable=True)

    # Provenance: which run wrote this evidence row, and what kind of producer it was.
    extraction_run_id = Column(
        Integer,
        ForeignKey("kg_extraction_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_kind = Column(String(24), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    relationship: "Relationship" = orm_relationship("Relationship", back_populates="evidences")
    mention: Optional["PersonMention"] = orm_relationship("PersonMention", back_populates="supported_evidences")
    block: "Block" = orm_relationship("Block")


    def __repr__(self) -> str:
        return f"<RelationshipEvidence(id={self.id}, rel_id={self.relationship_id}, block_id={self.block_id})>"
