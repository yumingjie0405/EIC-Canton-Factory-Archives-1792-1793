"""Canonical Person model."""
from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.kg.enums import IdentityStatus, ReviewStatusKG

if TYPE_CHECKING:
    from app.models.kg.mention import PersonMention
    from app.models.kg.relationship import Relationship
    from app.models.kg.authority import ExternalAuthority
    from app.models.kg.metrics import SNAGraphMetric


class Person(Base):
    """Canonical historical person entity."""

    __tablename__ = "persons"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)
    canonical_name_en = Column(String(255), nullable=True, index=True)
    canonical_name_zh = Column(String(255), nullable=True, index=True)
    display_name = Column(String(255), nullable=False, index=True)
    aliases = Column(JSON, nullable=True)  # List[str] or Dict
    primary_role = Column(String(100), nullable=True, index=True)
    #: Provenance of ``primary_role``: model_extracted / human_verified / heuristic /
    #: unknown. NULL means no role was ever recorded for this person, which the UI must
    #: report as "未知" rather than colour it as a real category.
    role_source = Column(String(24), nullable=True, index=True)
    organization = Column(String(255), nullable=True)
    birth_year = Column(SmallInteger, nullable=True)
    death_year = Column(SmallInteger, nullable=True)
    active_start = Column(Date, nullable=True)
    active_end = Column(Date, nullable=True)
    base_location = Column(String(255), nullable=True)
    biographical_notes = Column(Text, nullable=True)

    identity_status = Column(
        Enum(
            IdentityStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
            length=30,
        ),
        default=IdentityStatus.PROVISIONAL,
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


    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    mentions: List["PersonMention"] = relationship(
        "PersonMention",
        back_populates="person",
        cascade="all, delete-orphan",
    )
    out_relationships: List["Relationship"] = relationship(
        "Relationship",
        foreign_keys="Relationship.source_person_id",
        back_populates="source_person",
        cascade="all, delete-orphan",
    )
    in_relationships: List["Relationship"] = relationship(
        "Relationship",
        foreign_keys="Relationship.target_person_id",
        back_populates="target_person",
        cascade="all, delete-orphan",
    )
    external_authorities: List["ExternalAuthority"] = relationship(
        "ExternalAuthority",
        back_populates="person",
        cascade="all, delete-orphan",
    )
    sna_metrics: List["SNAGraphMetric"] = relationship(
        "SNAGraphMetric",
        back_populates="person",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "birth_year IS NULL OR death_year IS NULL OR birth_year <= death_year",
            name="chk_person_birth_death",
        ),
        CheckConstraint(
            "active_start IS NULL OR active_end IS NULL OR active_start <= active_end",
            name="chk_person_active_dates",
        ),
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="chk_person_display_name_nonempty",
        ),
    )

    def __repr__(self) -> str:
        return f"<Person(id={self.id}, display_name='{self.display_name}', role='{self.primary_role}')>"
