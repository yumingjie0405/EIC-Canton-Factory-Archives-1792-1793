"""SNAGraphMetric model for persisting social network analysis snapshots."""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.kg.person import Person


class SNAGraphMetric(Base):
    """Snapshot of Social Network Analysis (SNA) metrics for a person in a specific graph run."""

    __tablename__ = "sna_graph_metrics"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_id = Column(String(64), nullable=False, index=True)
    snapshot_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    graph_scope = Column(String(100), nullable=False)

    # SNA Metrics
    degree_centrality = Column(Float, nullable=True)
    betweenness_centrality = Column(Float, nullable=True)
    closeness_centrality = Column(Float, nullable=True)
    pagerank = Column(Float, nullable=True)
    community_id = Column(String(100), nullable=True, index=True)

    # Metadata
    algorithm_version = Column(String(100), nullable=False)
    parameters = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    person: "Person" = relationship("Person", back_populates="sna_metrics")

    __table_args__ = (
        UniqueConstraint("run_id", "person_id", name="uq_sna_run_person"),
        Index("ix_sna_metrics_run_scope", "run_id", "graph_scope"),
    )

    def __repr__(self) -> str:
        return f"<SNAGraphMetric(id={self.id}, person_id={self.person_id}, run_id='{self.run_id}')>"
