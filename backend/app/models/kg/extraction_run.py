"""Persistent ledger for group/document knowledge-graph extraction runs.

Why a ledger
------------
Batch extraction of thousands of blocks must be resumable and auditable: which run
touched which block, how many tokens were spent, why it stopped. Celery task state
lives in the broker and disappears; the research record cannot depend on it. These
two tables are therefore the source of truth for extraction progress.

States are deliberately explicit — a run that stopped because of budget, model
errors or malformed output is *not* "completed", and a manual pause is distinct from
an automatic one.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class KGExtractionRunStatus(str, enum.Enum):
    """Lifecycle of one extraction run."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"     # token/block budget exhausted
    PAUSED_ERROR = "paused_error"       # repeated transient failures
    PAUSED_FORMAT = "paused_format"     # repeated unparsable model output
    PAUSED_MANUAL = "paused_manual"     # human paused it
    COMPLETED = "completed"             # every block processed without failures
    PARTIAL = "partial"                 # every block attempted, some failed
    FAILED = "failed"                   # could not continue at all


class KGExtractionItemStatus(str, enum.Enum):
    """Outcome of one block inside one run."""

    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"   # already extracted with the same model/prompt
    FAILED = "failed"


class KGExtractionDispatchState(str, enum.Enum):
    """Whether the queued run actually reached the worker queue.

    ``pending``  - row exists, delivery has not been confirmed yet
    ``dispatched`` - the broker accepted the message
    ``failed``   - delivery failed; the row is kept so it can be re-delivered
    """

    PENDING = "pending"
    DISPATCHED = "dispatched"
    FAILED = "failed"


class KGExtractionRunTrigger(str, enum.Enum):
    """Who asked for this run (auto triggers are always tied to a real event)."""

    MANUAL = "manual"
    AUTO_IMPORT = "auto_import"      # successful Word import into a group
    AUTO_ATTACH = "auto_attach"      # existing document newly attached to a group
    RECOVERY = "recovery"            # re-delivery of an already queued run


class KGExtractionRun(Base):
    """One extraction attempt over a document or a whole document group."""

    __tablename__ = "kg_extraction_runs"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    scope_type = Column(String(16), nullable=False)  # "group" | "document"
    scope_id = Column(Integer, nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("research_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)

    status = Column(String(24), nullable=False, default=KGExtractionRunStatus.QUEUED.value, index=True)
    model = Column(String(128), nullable=True)
    prompt_hash = Column(String(64), nullable=True)

    #: Why this run exists and whether the queue actually received it.
    trigger = Column(String(24), nullable=False, default=KGExtractionRunTrigger.MANUAL.value)
    dispatch_state = Column(
        String(16),
        nullable=False,
        default=KGExtractionDispatchState.PENDING.value,
        index=True,
    )
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    dispatch_error = Column(Text, nullable=True)

    total_blocks = Column(Integer, nullable=False, default=0)
    processed_blocks = Column(Integer, nullable=False, default=0)
    skipped_blocks = Column(Integer, nullable=False, default=0)
    failed_blocks = Column(Integer, nullable=False, default=0)
    tokens_used = Column(Integer, nullable=False, default=0)

    budget_blocks = Column(Integer, nullable=True)
    budget_tokens = Column(Integer, nullable=True)
    #: How the budget was chosen: explicit request / deployment default / auto research.
    budget_source = Column(String(16), nullable=True)
    #: Origin of the extracted records (see ``SourceKind``). Nullable for legacy rows.
    source_kind = Column(String(24), nullable=True, index=True)
    #: What ``tokens_used`` measures (see ``TokenSource``); null = legacy/unknown.
    token_source = Column(String(16), nullable=True)
    max_consecutive_failures = Column(Integer, nullable=False, default=3)
    consecutive_failures = Column(Integer, nullable=False, default=0)

    cursor_block_id = Column(Integer, nullable=True)
    pause_reason = Column(Text, nullable=True)

    #: Reset bookkeeping. The run row is audit history and is never physically deleted;
    #: a cleanup records when and why its derived records were removed.
    reset_at = Column(DateTime, nullable=True)
    reset_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    items = relationship(
        "KGExtractionItem",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_kg_extraction_runs_scope", "scope_type", "scope_id"),
    )


class KGExtractionItem(Base):
    """Per-block outcome — the resumable unit and the idempotency key."""

    __tablename__ = "kg_extraction_items"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer, ForeignKey("kg_extraction_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_id = Column(Integer, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(16), nullable=False)
    tokens = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    run = relationship("KGExtractionRun", back_populates="items")

    __table_args__ = (
        UniqueConstraint("run_id", "block_id", name="uq_kg_extraction_items_run_block"),
    )


class KGExtractionResetAudit(Base):
    """Immutable record of one cleanup preview/execution.

    A cleanup can remove thousands of mentions and evidences, so it needs its own
    audit trail: which runs were targeted, what the preview found, what was actually
    deleted, what was protected and why, and who confirmed it.
    """

    __tablename__ = "kg_extraction_reset_audit"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    group_id = Column(
        Integer, ForeignKey("research_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: ``dry_run`` previews are audited too, so a rehearsal is never mistaken for a no-op.
    dry_run = Column(Boolean, nullable=False, default=True)
    run_ids = Column(JSON, nullable=False, default=list)
    counts = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=True)
    operator = Column(String(100), nullable=False, default="system")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


#: Tables created only by a reviewed migration (never by ``create_all``).
ALEMBIC_MANAGED_EXTRACTION_TABLES = frozenset(
    {"kg_extraction_runs", "kg_extraction_items", "kg_extraction_reset_audit"}
)
