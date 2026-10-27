"""Persisted research records (Graph RAG answers and Agent research runs).

Why this table exists
---------------------
A research answer is only useful if it can be reopened later with the evidence
it was actually based on. Re-running retrieval on reopen would silently change
the answer when the index, the model or the archival corpus changes, so the
answer **and** its evidence snapshot are stored together.

Schema ownership
----------------
This table is *not* created by ``Base.metadata.create_all()`` at application
startup. The startup call in :mod:`app.main` excludes every table named in
:data:`ALEMBIC_MANAGED_TABLES`; the DDL must arrive through a reviewed
migration instead (see ``backend/alembic_baseline/candidates/``).
"""
import enum
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum

from app.database import Base


#: Tables that must never be created implicitly by ``Base.metadata.create_all``.
#: Every table whose DDL is reviewed and shipped through a migration belongs here,
#: including the research-group tables (see ``app.models.research_group``).
ALEMBIC_MANAGED_TABLES = frozenset(
    {
        "research_sessions",
        "research_groups",
        "research_group_documents",
        # KG extraction ledger (0005): the audit trail must arrive by migration.
        "kg_extraction_runs",
        "kg_extraction_items",
        # Cleanup audit (0007): previews and executions are both recorded.
        "kg_extraction_reset_audit",
        # Workflow tables (0009): review audit, reproducible snapshots, block versions.
        "kg_review_audit",
        "kg_graph_snapshots",
        "kg_block_versions",
    }
)


class ResearchMode(str, enum.Enum):
    """Which research pipeline produced the record."""

    RAG = "rag"
    AGENT = "agent"


class ResearchStatus(str, enum.Enum):
    """Outcome of the research run, stored verbatim (never upgraded to success)."""

    COMPLETED = "completed"
    DEGRADED = "degraded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"


class ResearchSession(Base):
    """A single saved research question, answer and evidence snapshot."""

    __tablename__ = "research_sessions"
    __allow_unmapped__ = True

    # The primary key already has a unique index; no separate index on id.
    id = Column(Integer, primary_key=True)
    mode = Column(
        SAEnum(
            ResearchMode,
            native_enum=False,
            create_constraint=True,
            length=16,
            # Store the *values* (lower case), not the member names, so the
            # database, the API and the CHECK constraint all agree.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False, default="")
    status = Column(
        SAEnum(
            ResearchStatus,
            native_enum=False,
            create_constraint=True,
            length=24,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ResearchStatus.COMPLETED,
    )
    evidence_sufficient = Column(Boolean, nullable=False, default=False)

    # Generation provenance (mirrors schemas.rag.AnswerGeneration)
    generation_mode = Column(String(32), nullable=True)
    model_name = Column(String(128), nullable=True)
    failure_reason = Column(String(64), nullable=True)
    failure_message = Column(Text, nullable=True)
    failure_detail = Column(Text, nullable=True)

    # Citation integrity (mirrors schemas.rag.CitationIntegrity)
    citation_valid = Column(Boolean, nullable=True)
    citation_invalid_ids = Column(JSON, nullable=True)
    citation_available_count = Column(Integer, nullable=True)

    # Evidence snapshot: full serialised RagSourceEvidence / entity / relation list
    sources = Column(JSON, nullable=False, default=list)
    entities = Column(JSON, nullable=True)
    relationships = Column(JSON, nullable=True)
    intent = Column(JSON, nullable=True)
    warnings = Column(JSON, nullable=True)

    elapsed_ms = Column(Float, nullable=True)
    #: Owning research group, when the run was scoped to a document group.
    group_id = Column(
        Integer,
        ForeignKey("research_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: Group name captured at run time, so history stays readable after a rename.
    group_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_research_sessions_created_at", "created_at"),
        Index("ix_research_sessions_mode_created_at", "mode", "created_at"),
        Index("ix_research_sessions_status", "status"),
    )

    def snapshot(self) -> Dict[str, Any]:
        """Return the stored evidence snapshot as plain JSON-serialisable data."""
        return {
            "sources": self.sources or [],
            "entities": self.entities or [],
            "relationships": self.relationships or [],
            "intent": self.intent,
            "warnings": self.warnings or [],
        }

    def __repr__(self) -> str:
        return "<ResearchSession(id=%s, mode=%s, status=%s)>" % (
            self.id,
            getattr(self.mode, "value", self.mode),
            getattr(self.status, "value", self.status),
        )
