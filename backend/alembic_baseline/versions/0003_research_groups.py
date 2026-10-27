"""Add research groups, their document membership, and group-scoped research.

Creates
-------
* ``research_groups``            – a named, curated corpus
* ``research_group_documents``   – membership (unique per group+document)
* ``documents.content_hash``     – content identity used for de-duplication
* ``research_sessions.group_id`` – which group a saved run belongs to
* ``research_sessions.group_name`` – the name captured at run time

The workspace table never duplicates archival data: a membership row only links
an existing document, so removing a document from a group deletes one row and
leaves the document, its OCR output and other groups untouched.

Downgrade
---------
Reverses exactly what this revision added (the new tables and the new columns).
It never touches the archival baseline tables.
"""
from alembic import op

revision = "0003_research_groups"
down_revision = "0002_research_sessions"
branch_labels = None
depends_on = None


GROUPS_DDL = """
CREATE TABLE research_groups (
    id SERIAL NOT NULL,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_research_groups_name UNIQUE (name)
)
"""

MEMBERS_DDL = """
CREATE TABLE research_group_documents (
    id SERIAL NOT NULL,
    group_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    import_status VARCHAR(16) NOT NULL,
    import_error TEXT,
    last_batch_id VARCHAR(36),
    added_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_research_group_documents_group_document UNIQUE (group_id, document_id),
    CONSTRAINT groupimportstatus CHECK (
        import_status IN ('pending', 'queued', 'processing', 'ready', 'failed')
    ),
    FOREIGN KEY(group_id) REFERENCES research_groups (id) ON DELETE CASCADE,
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
)
"""

STATEMENTS = (
    GROUPS_DDL,
    MEMBERS_DDL,
    "CREATE INDEX ix_research_group_documents_group_id ON research_group_documents (group_id)",
    "CREATE INDEX ix_research_group_documents_document_id ON research_group_documents (document_id)",
    "CREATE INDEX ix_research_group_documents_batch ON research_group_documents (last_batch_id)",
    "ALTER TABLE documents ADD COLUMN content_hash VARCHAR(64)",
    "CREATE INDEX ix_documents_content_hash ON documents (content_hash)",
    "ALTER TABLE research_sessions ADD COLUMN group_id INTEGER",
    "ALTER TABLE research_sessions ADD COLUMN group_name VARCHAR(120)",
    "ALTER TABLE research_sessions ADD CONSTRAINT fk_research_sessions_group_id "
    "FOREIGN KEY(group_id) REFERENCES research_groups (id) ON DELETE SET NULL",
    "CREATE INDEX ix_research_sessions_group_id ON research_sessions (group_id)",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP INDEX IF EXISTS ix_research_sessions_group_id",
        "ALTER TABLE research_sessions DROP CONSTRAINT IF EXISTS fk_research_sessions_group_id",
        "ALTER TABLE research_sessions DROP COLUMN IF EXISTS group_name",
        "ALTER TABLE research_sessions DROP COLUMN IF EXISTS group_id",
        "DROP INDEX IF EXISTS ix_documents_content_hash",
        "ALTER TABLE documents DROP COLUMN IF EXISTS content_hash",
        "DROP TABLE research_group_documents",
        "DROP TABLE research_groups",
    ):
        op.execute(statement)
