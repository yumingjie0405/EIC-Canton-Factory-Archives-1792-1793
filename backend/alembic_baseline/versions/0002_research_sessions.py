"""Add ``research_sessions`` (persisted research history).

Depends on :data:`0001_baseline_ocr_schema` — the baseline that
recreates the audited pre-existing schema — so this revision never becomes a
second root and never leaves a dangling ``down_revision``.

The DDL is generated from ``app/models/research.py`` with SQLAlchemy's
PostgreSQL dialect (see ``alembic_baseline/candidates/0001_research_sessions.ddl.sql``),
so it matches the ORM model exactly: four indexes, two CHECK constraints and the
serial primary key.

Downgrade
---------
Only the table created by this revision is dropped. It holds no archival
content (the evidence is stored as a snapshot *copy*; the source of truth for
documents/blocks remains untouched), so this reversal is safe and additive.
"""
from alembic import op

revision = "0002_research_sessions"
down_revision = "0001_baseline_ocr_schema"
branch_labels = None
depends_on = None


DDL = """
CREATE TABLE research_sessions (
    id SERIAL NOT NULL,
    mode VARCHAR(16) NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    status VARCHAR(24) NOT NULL,
    evidence_sufficient BOOLEAN NOT NULL,
    generation_mode VARCHAR(32),
    model_name VARCHAR(128),
    failure_reason VARCHAR(64),
    failure_message TEXT,
    failure_detail TEXT,
    citation_valid BOOLEAN,
    citation_invalid_ids JSON,
    citation_available_count INTEGER,
    sources JSON NOT NULL,
    entities JSON,
    relationships JSON,
    intent JSON,
    warnings JSON,
    elapsed_ms FLOAT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT researchmode CHECK (mode IN ('rag', 'agent')),
    CONSTRAINT researchstatus CHECK (status IN ('completed', 'degraded', 'insufficient_evidence', 'failed'))
)
"""

INDEXES = (
    "CREATE INDEX ix_research_sessions_created_at ON research_sessions (created_at)",
    "CREATE INDEX ix_research_sessions_mode_created_at ON research_sessions (mode, created_at)",
    "CREATE INDEX ix_research_sessions_status ON research_sessions (status)",
)


def upgrade() -> None:
    op.execute(DDL)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE research_sessions")
