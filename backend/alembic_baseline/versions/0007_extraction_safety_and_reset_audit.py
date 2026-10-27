"""Extraction safety: budget provenance, reset bookkeeping and a cleanup audit table.

Why this migration exists
-------------------------
Run #9 extracted 14,620 blocks with ``budget_blocks = NULL``, i.e. with no ceiling at
all. Nothing in the schema recorded *why* a budget was what it was, and a cleanup of
derived records had nowhere to be recorded. This revision adds exactly those facts:

* ``kg_extraction_runs.budget_source`` — explicit request vs. deployment default vs.
  automatic research, so an audit can tell how a run was bounded.
* ``kg_extraction_runs.reset_at / reset_reason`` — a run row is history and is never
  deleted; a cleanup marks it instead.
* ``kg_extraction_reset_audit`` — one immutable row per cleanup preview or execution,
  with the targeted run ids and the per-category counts.

Existing rows are left untouched: ``budget_source`` stays NULL for historical runs
(the truth is "unknown"), which the UI must render as "unknown" rather than guessing.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_extraction_safety"
down_revision = "0006_kg_auto_extract_on_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kg_extraction_runs", sa.Column("budget_source", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "kg_extraction_runs", sa.Column("reset_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "kg_extraction_runs", sa.Column("reset_reason", sa.Text(), nullable=True)
    )

    op.create_table(
        "kg_extraction_reset_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("research_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("run_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "operator", sa.String(length=100), nullable=False, server_default="system"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_kg_extraction_reset_audit_group_id",
        "kg_extraction_reset_audit",
        ["group_id"],
    )
    op.create_index(
        "ix_kg_extraction_reset_audit_document_id",
        "kg_extraction_reset_audit",
        ["document_id"],
    )
    op.create_index(
        "ix_kg_extraction_reset_audit_created_at",
        "kg_extraction_reset_audit",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kg_extraction_reset_audit_created_at",
        table_name="kg_extraction_reset_audit",
    )
    op.drop_index(
        "ix_kg_extraction_reset_audit_document_id",
        table_name="kg_extraction_reset_audit",
    )
    op.drop_index(
        "ix_kg_extraction_reset_audit_group_id",
        table_name="kg_extraction_reset_audit",
    )
    op.drop_table("kg_extraction_reset_audit")
    op.drop_column("kg_extraction_runs", "reset_reason")
    op.drop_column("kg_extraction_runs", "reset_at")
    op.drop_column("kg_extraction_runs", "budget_source")
