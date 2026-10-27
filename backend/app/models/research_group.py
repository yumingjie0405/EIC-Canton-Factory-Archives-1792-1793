"""Research groups: a curated set of documents studied together.

Why this exists
---------------
Research answers must be traceable to a *bounded* corpus. A group is that
boundary: it lists documents (shared documents may belong to several groups) and
every research entry point is expected to search only inside it.

Schema ownership
----------------
``research_groups`` and ``research_group_documents`` are Alembic-managed (see
:data:`app.models.research.ALEMBIC_MANAGED_TABLES`); ``create_all()`` at startup
never creates them.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship

from app.database import Base

#: Tables created only by a reviewed migration.
ALEMBIC_MANAGED_GROUP_TABLES = frozenset(
    {"research_groups", "research_group_documents"}
)


class GroupImportStatus(str, enum.Enum):
    """Lifecycle of one document *inside* one group."""

    PENDING = "pending"        # uploaded / attached, processing not started
    QUEUED = "queued"          # OCR task accepted by the queue
    PROCESSING = "processing"  # OCR running
    READY = "ready"            # text blocks exist and are searchable
    FAILED = "failed"          # processing failed, reason in import_error


class ResearchGroup(Base):
    """A named set of documents that research questions are scoped to."""

    __tablename__ = "research_groups"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    #: Whether a successful Word import / new membership in this group may queue
    #: automatic knowledge-graph extraction. It only ever decides *participation*;
    #: the deployment master switch and the per-run budgets still apply.
    auto_extract = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    members = relationship(
        "ResearchGroupDocument",
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ResearchGroupDocument(Base):
    """Membership row: one document in one group.

    Removing a membership deletes **only this row** — the document, its pages,
    blocks, OCR results and other groups' memberships are untouched.
    """

    __tablename__ = "research_group_documents"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    group_id = Column(
        Integer,
        ForeignKey("research_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    import_status = Column(
        SAEnum(
            GroupImportStatus,
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda cls: [member.value for member in cls],
        ),
        nullable=False,
        default=GroupImportStatus.PENDING,
    )
    import_error = Column(Text, nullable=True)
    last_batch_id = Column(String(36), nullable=True, index=True)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    group = relationship("ResearchGroup", back_populates="members")
    document = relationship("Document")

    __table_args__ = (
        UniqueConstraint(
            "group_id", "document_id", name="uq_research_group_documents_group_document"
        ),
        Index("ix_research_group_documents_batch", "last_batch_id"),
    )
