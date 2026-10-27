"""Add ``DOCX`` to the documents.file_type enum.

``documents`` is created by ``create_all()`` rather than by this chain, so a fresh
database picks up the new value automatically. Databases that already exist keep
the old ``documenttype`` enum, and writing a Word document there would fail with
"invalid input value for enum documenttype". This migration adds the value
idempotently so an existing installation can store .docx files.

Downgrade is intentionally a no-op: PostgreSQL cannot remove a value from an enum
type. Leaving ``DOCX`` in place is harmless once the application stops writing it.
"""
from alembic import op

revision = "0004_document_type_docx"
down_revision = "0003_research_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'documenttype')
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_enum e
                   JOIN pg_type t ON t.oid = e.enumtypid
                   WHERE t.typname = 'documenttype' AND e.enumlabel = 'DOCX'
               )
            THEN
                ALTER TYPE documenttype ADD VALUE 'DOCX';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """No-op: PostgreSQL cannot drop a single enum value."""
    pass
