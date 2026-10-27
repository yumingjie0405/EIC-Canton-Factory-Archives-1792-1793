"""Audit trail and reproducible snapshots for the research workflow.

Two tables, two jobs:

* ``kg_review_audit`` — every human decision (relationship review, mention
  confirmation, batch disambiguation) with before/after states, so a decision can be
  inspected and a batch can be reverted. ``entity_operations_audit`` already covers
  merges/splits but requires a person on both sides, which a relationship review or a
  batch of mentions does not have.
* ``kg_graph_snapshots`` — a *hybrid* snapshot: descriptive metadata (scope, filters,
  algorithm, provenance) plus the materialised node/edge list and metrics that were
  actually computed. A row with only a timestamp would not be reproducible, and the
  current data must never be presented as a past state.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship as orm_relationship

from app.database import Base


class KGReviewAudit(Base):
    """One recorded human decision in the review workflow."""

    __tablename__ = "kg_review_audit"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)

    #: relationship_review / mention_confirm / mention_reject / mention_defer /
    #: mention_batch_link / mention_batch_revert
    operation_type = Column(String(40), nullable=False, index=True)

    group_id = Column(
        Integer, ForeignKey("research_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    relationship_id = Column(
        Integer, ForeignKey("relationships.id", ondelete="SET NULL"), nullable=True, index=True
    )
    person_id = Column(
        Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mention_id = Column(
        Integer, ForeignKey("person_mentions.id", ondelete="SET NULL"), nullable=True
    )

    #: Groups the rows written by one batch so it can be reverted as a unit.
    batch_id = Column(String(64), nullable=True, index=True)

    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    affected_counts = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    operator = Column(String(100), nullable=False, default="system")
    is_reverted = Column(Boolean, nullable=False, default=False, index=True)
    reverted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    relationship = orm_relationship("Relationship")
    person = orm_relationship("Person")


class KGGraphSnapshot(Base):
    """A stored, explainable view of one group's graph at one moment."""

    __tablename__ = "kg_graph_snapshots"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)

    #: Caller-supplied identifier (kept for the existing snapshot API contract).
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=True)
    note = Column(Text, nullable=True)

    group_id = Column(
        Integer, ForeignKey("research_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    group_name = Column(String(200), nullable=True)

    #: How the graph was selected. Without these a snapshot cannot be explained.
    view = Column(String(20), nullable=False, default="all")
    filters = Column(JSON, nullable=True)
    algorithm_version = Column(String(100), nullable=True)
    parameters = Column(JSON, nullable=True)

    #: Materialised result: the nodes and edges that were actually counted.
    node_count = Column(Integer, nullable=False, default=0)
    edge_count = Column(Integer, nullable=False, default=0)
    persons = Column(JSON, nullable=False, default=list)
    relationships = Column(JSON, nullable=False, default=list)
    metrics_summary = Column(JSON, nullable=True)
    excluded = Column(JSON, nullable=True)

    #: Provenance of the stored rows: this is what makes "mock" visible in history.
    data_trust = Column(JSON, nullable=True)
    extraction_run_ids = Column(JSON, nullable=True)

    #: Honest capability statement: described / rebuildable / recomputable.
    capability = Column(String(24), nullable=False, default="described")

    created_by = Column(String(100), nullable=False, default="system")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (Index("ix_kg_graph_snapshots_group_created", "group_id", "created_at"),)


class KGBlockVersion(Base):
    """Version marker for a block's text, so evidence can name the text it read.

    Full version management is a later stage; this table exists now so that new
    extractions can record *which* text version they consumed instead of silently
    assuming the current text is the historical one.
    """

    __tablename__ = "kg_block_versions"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    block_id = Column(
        Integer, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number = Column(Integer, nullable=False, default=1)
    content_hash = Column(String(64), nullable=False, index=True)
    text_excerpt = Column(Text, nullable=True)
    source = Column(String(40), nullable=False, default="import")
    supersedes_id = Column(Integer, ForeignKey("kg_block_versions.id", ondelete="SET NULL"), nullable=True)
    #: True when the original version of a pre-existing block cannot be established.
    is_uncertain = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


#: Tables that only a reviewed migration may create.
ALEMBIC_MANAGED_WORKFLOW_TABLES = frozenset(
    {"kg_review_audit", "kg_graph_snapshots", "kg_block_versions"}
)
